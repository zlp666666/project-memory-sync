#!/usr/bin/env python3
"""Inspect all tracked files and reachable Git blobs before distribution. No credential access."""
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "credential_like": re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"),
    "personal_machine_path": re.compile(r"(?i)[a-z]:[\\/](?:users[\\/][^<>\\/\s]+|AI工作空间)(?:[\\/]|$)"),
    "private_network_share": re.compile(r"\\\\(?:\d{1,3}\.){3}\d{1,3}\\"),
    "chinese_phone": re.compile(r"(?<![\dA-Za-z])1[3-9]\d{9}(?![\dA-Za-z])"),
    "private_data_filename": re.compile(r"(?i)(?:s7-library\.json|candidates_raw\.json|inventory\.csv|state\.db|auth\.json)$"),
}
ALLOWED_ROOT = {"SKILL.md", "README.md", "CHANGELOG.md", "VERSION", ".gitignore", ".gitattributes", "LICENSE"}
ALLOWED_PREFIXES = ("scripts/", "tests/", "references/", "examples/", "agents/", ".github/")


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout


def inspect_text(text, label):
    findings = []
    for name, pattern in PATTERNS.items():
        if name == "private_data_filename":
            continue
        for match in pattern.finditer(text):
            findings.append({"object": label, "reason": name, "line": text.count("\n", 0, match.start()) + 1})
    return findings


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    findings = []
    try:
        tracked = git("ls-files", "-z").decode("utf-8").split("\0")
        for name in filter(None, tracked):
            if name not in ALLOWED_ROOT and not name.startswith(ALLOWED_PREFIXES):
                findings.append({"object": name, "reason": "unexpected_tracked_file"})
            if PATTERNS["private_data_filename"].search(name) or name.endswith((".db", ".sqlite", ".jsonl", ".log", ".zip", ".bundle", ".bak")):
                findings.append({"object": name, "reason": "private_or_runtime_file"})
            raw = (ROOT / name).read_bytes()
            try:
                findings.extend(inspect_text(raw.decode("utf-8"), name))
            except UnicodeDecodeError:
                findings.append({"object": name, "reason": "unreviewed_binary"})
            # The staged version must also be inspected if it differs from working copy.
            staged = git("show", ":" + name)
            if staged != raw:
                try:
                    findings.extend(inspect_text(staged.decode("utf-8"), "index:" + name))
                except UnicodeDecodeError:
                    findings.append({"object": name, "reason": "unreviewed_staged_binary"})
        history = git("rev-list", "--objects", "--all").decode("utf-8").splitlines()
        blob_count = 0
        for line in history:
            oid, _, filename = line.partition(" ")
            if git("cat-file", "-t", oid).strip() != b"blob":
                continue
            blob_count += 1
            if filename and (PATTERNS["private_data_filename"].search(filename) or filename.endswith((".db", ".jsonl", ".log", ".bak"))):
                findings.append({"object": oid, "reason": "private_data_in_history"})
            raw = git("cat-file", "blob", oid)
            try:
                findings.extend(inspect_text(raw.decode("utf-8"), "history:" + oid))
            except UnicodeDecodeError:
                findings.append({"object": oid, "reason": "unreviewed_history_binary"})
        result = {"ok": not findings, "tracked_files": len(list(filter(None, tracked))), "historical_blobs": blob_count,
                  "findings": findings, "limitation": "Pattern and allowlist audit; human review of all files remains required."}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 2
    except (OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": "Git audit could not complete", "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

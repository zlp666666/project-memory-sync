#!/usr/bin/env python3
"""Preview local setup/install; doctor is read-only. Python 3.10+, no network."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import zipfile
from contextlib import redirect_stdout

if __package__:
    from .core import SyncError
else:
    from core import SyncError

ROOT = Path(__file__).resolve().parents[1]
# Audited deployment allowlist: never walk a checkout or include local configuration.
FILES = (
    "SKILL.md", "README.md", "VERSION", "CHANGELOG.md", ".gitignore",
    "scripts/__init__.py", "scripts/core.py", "scripts/legacy.py",
    "scripts/memory_sync.py", "scripts/bootstrap.py",
    "references/clients.md", "references/contract.md", "references/migration.md",
    "references/validation.md", "references/setup.md", "references/handoff.md",
    "examples/create.json", "examples/append.json", "examples/authorization.json",
    "examples/connection.example.json",
)


class SetupError(ValueError):
    pass


def safe_path(value, *, exists=False, directory=False):
    """Reject links/reparse points in every existing component, including junctions."""
    p = Path(value).expanduser()
    if not p.is_absolute() or ".." in p.parts:
        raise SetupError("ABSOLUTE_PATH_REQUIRED")
    for item in (*reversed(p.parents), p):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SetupError("LINK_OR_REPARSE_PATH_REJECTED")
    if exists and not p.exists():
        raise SetupError("EXISTING_PATH_REQUIRED")
    if directory and not p.is_dir():
        raise SetupError("EXISTING_DIRECTORY_REQUIRED")
    return p.resolve()


def within(child, parent):
    return child == parent or parent in child.parents


def read_json(path):
    value = json.loads(safe_path(path, exists=True).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise SetupError("CONFIG_OBJECT_REQUIRED")
    return value


def validate_connection(config, archive_override=None):
    missing = [key for key in ("workspace", "archive", "store_id", "access") if not config.get(key)]
    if missing:
        raise SetupError("MISSING: " + ",".join(missing))
    if config.get("connection_schema") != 1 or config["access"] not in ("read_only", "read_write"):
        raise SetupError("INVALID_CONNECTION_SCHEMA_OR_ACCESS")
    workspace = safe_path(config["workspace"], exists=True, directory=True)
    archive = safe_path(config["archive"], exists=True, directory=True)
    if archive_override and safe_path(archive_override) != archive:
        raise SetupError("ARCHIVE_OVERRIDE_CONFLICT")
    if not within(archive, workspace):
        raise SetupError("ARCHIVE_OUTSIDE_WORKSPACE")
    if within(archive, ROOT):
        raise SetupError("PRIVATE_DATA_IN_CODE_REPOSITORY_FORBIDDEN")
    datafile = safe_path(archive / "logs" / "s7-library.json", exists=True)
    if not datafile.is_file() or datafile.stat().st_nlink != 1:
        raise SetupError("ARCHIVE_MUST_BE_REGULAR_SINGLE_LINK_FILE")
    lock = safe_path(datafile.with_name(datafile.name + ".lock"))
    if lock.exists() and (not lock.is_file() or lock.stat().st_nlink != 1):
        raise SetupError("LOCK_MUST_BE_REGULAR_SINGLE_LINK_FILE")
    backups = safe_path(datafile.parent / ".memory-sync-backups")
    if backups.exists():
        if not backups.is_dir():
            raise SetupError("BACKUP_DIRECTORY_REQUIRED")
        for item in backups.iterdir():
            leaf = safe_path(item, exists=True)
            if not leaf.is_file() or leaf.stat().st_nlink != 1:
                raise SetupError("BACKUP_MUST_BE_REGULAR_SINGLE_LINK_FILE")
    if __package__:
        from .core import Store
    else:
        from core import Store
    data = Store(datafile).read()
    if data["store_id"] != config["store_id"]:
        raise SetupError("STORE_ID_MISMATCH")
    return workspace, archive, data


def connection(args):
    configpath = safe_path(args.config or ROOT / "local-config.json")
    config = read_json(configpath) if configpath.exists() else {}
    # Match the archive CLI precedence, and validate any configured identity below.
    archive = args.archive or os.environ.get("PROJECT_MEMORY_ARCHIVE") or config.get("archive")
    workspace = args.workspace or config.get("workspace")
    missing = [key for key, value in (("workspace", workspace), ("archive", archive)) if not value]
    if missing:
        raise SetupError("MISSING: " + ",".join(missing))
    if config.get("connection_schema") == 1:
        validate_connection(config, archive)
        if args.workspace and safe_path(args.workspace) != safe_path(config["workspace"]):
            raise SetupError("WORKSPACE_OVERRIDE_CONFLICT")
        if args.access and args.access != config["access"]:
            raise SetupError("ACCESS_OVERRIDE_CONFLICT: use a new config path")
    workspace = safe_path(workspace, exists=True, directory=True)
    archive = safe_path(archive, exists=True, directory=True)
    # First connection reads only a selected existing schema3 archive; never initializes it.
    if __package__:
        from .core import Store
    else:
        from core import Store
    datafile = safe_path(archive / "logs" / "s7-library.json", exists=True)
    data = Store(datafile).read()
    expected = args.store_id or config.get("store_id") or data["store_id"]
    access = args.access or config.get("access") or "read_only"
    proposed = {"connection_schema": 1, "workspace": str(workspace), "archive": str(archive),
                "store_id": expected, "access": access}
    validate_connection(proposed, archive)
    return configpath, proposed


def doctor(args):
    configpath, config = connection(args)
    workspace, archive, _ = validate_connection(config)
    # Reuse the established CLI/status protocol and emit only identity/count metadata.
    if __package__:
        from .memory_sync import main as cli_main
    else:
        from memory_sync import main as cli_main
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = cli_main(["--archive", str(archive), "status"])
    status = json.loads(buffer.getvalue())
    if result:
        raise SetupError("STATUS_FAILED")
    return {"ok": True, "python": ".".join(map(str, sys.version_info[:3])),
            "version": status["version"], "schema_version": status["schema_version"],
            "store_id": status["store_id"], "revision": status["revision"],
            "records": status["records"], "events": status["events"],
            "access": config["access"], "config_saved": configpath.is_file(),
            "scope": "archive inside selected workspace", "write_probe": "not_performed",
            "client_discovery": "not_tested"}


def setup(args):
    path, config = connection(args)
    raw = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        existing = read_json(path)
        if existing != config:
            raise SetupError("CONFIG_EXISTS_CONFLICT: choose a new config path; existing file preserved")
        return {"ok": True, "mode": "unchanged", "changed": False, "store_id": config["store_id"]}
    if not path.parent.is_dir():
        raise SetupError("CONFIG_PARENT_MUST_EXIST")
    if within(path, Path(config["archive"])):
        raise SetupError("CONFIG_INSIDE_ARCHIVE_REJECTED")
    if not args.apply:
        return {"ok": True, "mode": "plan", "changed": False, "would_create": "local connection config",
                "store_id": config["store_id"], "access": config["access"]}
    safe_path(path)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"ok": True, "mode": "created", "changed": True, "store_id": config["store_id"]}


def payload():
    return {name: safe_path(ROOT / name, exists=True).read_bytes() for name in FILES}


def install(args):
    skills = safe_path(args.skills_root, exists=True, directory=True)
    target = safe_path(skills / "project-memory-sync")
    if within(target, ROOT) or within(ROOT, target):
        raise SetupError("INSTALL_SOURCE_OVERLAP")
    content = payload()
    if target.exists():
        if not target.is_dir():
            raise SetupError("INSTALL_TARGET_CONFLICT")
        actual = {}
        for folder, directories, files in os.walk(target, followlinks=False):
            for name in directories + files:
                safe_path(Path(folder) / name)
            for name in files:
                p = Path(folder) / name
                if p.stat().st_nlink != 1:
                    raise SetupError("INSTALL_TARGET_LINK_CONFLICT")
                relative = p.relative_to(target).as_posix()
                if relative == "local-config.json":
                    continue
                if relative.startswith("scripts/__pycache__/") and relative.endswith(".pyc"):
                    continue
                if relative not in content:
                    raise SetupError("INSTALL_TARGET_CONFLICT: existing content preserved")
                actual[relative] = p.read_bytes()
        if actual != content:
            raise SetupError("INSTALL_TARGET_CONFLICT: existing content preserved")
        return {"ok": True, "mode": "unchanged", "changed": False, "files": len(content),
                "client_discovery": "not_tested"}
    if not args.apply:
        return {"ok": True, "mode": "plan", "changed": False, "files": len(content),
                "client_discovery": "not_tested"}
    # Exclusive directory creation prevents overwriting even an empty existing destination.
    target.mkdir()
    for name, raw in content.items():
        dest = safe_path(target / name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as stream:
            stream.write(raw)
    return {"ok": True, "mode": "created", "changed": True, "files": len(content),
            "client_discovery": "not_tested"}


def package(args):
    dest = safe_path(args.output)
    if dest.exists() or not dest.parent.is_dir():
        raise SetupError("NEW_PACKAGE_PATH_REQUIRED")
    content = payload()
    if not args.apply:
        return {"ok": True, "mode": "plan", "changed": False, "files": sorted(content)}
    with dest.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, raw in content.items():
                info = zipfile.ZipInfo("project-memory-sync/" + name, (2026, 1, 1, 0, 0, 0))
                bundle.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    return {"ok": True, "mode": "created", "changed": True, "files": len(content),
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "doctor"):
        command = commands.add_parser(name, help="connect existing memory; no archive creation")
        command.add_argument("--config", help="explicit config, or skill/local-config.json")
        command.add_argument("--workspace", help="existing absolute workspace")
        command.add_argument("--archive", help="existing archive; also supports PROJECT_MEMORY_ARCHIVE")
        command.add_argument("--store-id", help="optional expected archive UUID")
        command.add_argument("--access", choices=("read_only", "read_write"))
        if name == "setup":
            command.add_argument("--apply", action="store_true", help="create config exclusively; default preview")
    command = commands.add_parser("install", help="copy reviewed code into explicitly selected skills root")
    command.add_argument("--skills-root", required=True, help="existing absolute directory, no client guessing")
    command.add_argument("--apply", action="store_true", help="create new skill directory; default preview")
    command = commands.add_parser("package", help="allowlisted ZIP without private config/data")
    command.add_argument("--output", required=True)
    command.add_argument("--apply", action="store_true", help="create new ZIP; default preview")
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 10):
            raise SetupError("PYTHON_3_10_REQUIRED")
        result = globals()[args.command](args)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, SyncError) as exc:
        # Avoid echoing arbitrary config contents or private path exception text.
        error = str(exc) if isinstance(exc, SetupError) else type(exc).__name__
        if isinstance(exc, SyncError):
            error = str(exc).split(":", 1)[0]
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

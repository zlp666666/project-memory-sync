"""CLI acceptance, stable-ID view and failure recovery on synthetic local archives."""
import json
import os
import re
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.core import Store, atomic_write, canonical, empty_store
from scripts.memory_sync import render


class CLITests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT.parent / "runtime"
        runtime.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="cli-", dir=runtime)
        self.addCleanup(self.tmp.cleanup)
        self.archive = Path(self.tmp.name)
        (self.archive / "logs").mkdir()
        self.path = self.archive / "logs" / "s7-library.json"
        self.path.write_text(canonical(empty_store()), encoding="utf-8")
        self.store = Store(self.path)

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "memory_sync.py"),
                               "--archive", str(self.archive), *args], capture_output=True, encoding="utf-8")
        return proc.returncode, json.loads(proc.stdout)

    def create(self, request="new"):
        return self.store.apply({"request_id": request, "action": "create", "text": "An idea without outcomes",
            "authorization": {"basis": "explicit_user", "reference": "synthetic:user-1", "quote": "Save this idea"},
            "source": {"ref": "synthetic:user-1", "quote": "A garden reminder", "evidence_status": "verified"},
            "record": {"name": "Garden reminder"}})

    def test_cli_new_process_search_history_and_export_import(self):
        saved = self.create()
        code, found = self.run_cli("search", "--query", "Garden reminder")
        self.assertEqual(code, 0)
        self.assertEqual(found["results"][0]["id"], saved["record_id"])
        code, hist = self.run_cli("history", "--id", saved["record_id"])
        self.assertEqual(hist["events"][0]["source"]["quote"], "A garden reminder")
        target = self.archive / "roundtrip.json"
        self.assertEqual(self.run_cli("export", "--output", str(target))[0], 0)
        auth = self.archive / "authorization.json"
        auth.write_text(json.dumps({"basis": "explicit_user", "reference": "synthetic:u2", "quote": "Import this snapshot"}))
        code, result = self.run_cli("import", "--file", str(target), "--expected-revision", "1", "--authorization-file", str(auth))
        self.assertEqual(code, 0)
        self.assertTrue(result["duplicate"])
        self.assertEqual(self.store.read()["revision"], 1)

    def test_export_rejects_overwriting_existing_asset(self):
        dest = self.archive / "already.json"
        dest.write_text("keep existing")
        code, result = self.run_cli("export", "--output", str(dest))
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(dest.read_text(), "keep existing")

    def test_atomic_new_output_cannot_overwrite_even_under_race(self):
        dest = self.archive / "asset.json"
        dest.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            atomic_write(dest, b"changed", overwrite=False)
        self.assertEqual(dest.read_bytes(), b"original")

    def test_migrate_plan_apply_and_idempotent_retry(self):
        one = {"name": "Garden reminder", "depth": "灵感", "status": "暂时搁置"}
        legacy = {"user_lib": [one], "ai_zone": [], "qa_zone": [], "incub": [dict(one, incub_rank=1)]}
        self.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        code, plan = self.run_cli("migrate")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), legacy)
        auth = self.archive / "auth.json"
        auth.write_text(json.dumps({"basis": "explicit_user", "reference": "synthetic:u", "quote": "Migrate the example"}))
        code, result = self.run_cli("migrate", "--apply", "--authorization-file", str(auth), "--expected-plan-hash", plan["plan_hash"])
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "applied")
        self.assertEqual(self.run_cli("verify-backup", "--sha256", result["backup_sha256"])[0], 0)
        self.assertTrue(self.run_cli("migrate", "--apply", "--authorization-file", str(auth))[1]["duplicate"])

    def test_migration_plan_changes_are_rejected_without_partial_write(self):
        legacy = {"user_lib": [{"name": "A"}], "incub": [], "ai_zone": []}
        self.path.write_text(json.dumps(legacy))
        _, plan = self.run_cli("migrate")
        legacy["user_lib"][0]["name"] = "B"
        changed = json.dumps(legacy).encode()
        self.path.write_bytes(changed)
        auth = self.archive / "auth.json"
        auth.write_text(json.dumps({"basis": "explicit_user", "reference": "synthetic:u", "quote": "Migrate the example"}))
        code, result = self.run_cli("migrate", "--apply", "--authorization-file", str(auth), "--expected-plan-hash", plan["plan_hash"])
        self.assertEqual(code, 2)
        self.assertIn("MIGRATION_PLAN_CHANGED", result["error"])
        self.assertEqual(self.path.read_bytes(), changed)

    def test_replace_failure_leaves_exact_previous_document_and_restorable_backup(self):
        previous = self.path.read_bytes()
        real_replace = os.replace
        def failing_replace(src, dest):
            if Path(dest) == self.path:
                raise OSError("synthetic disk failure")
            return real_replace(src, dest)
        with patch("scripts.core.os.replace", failing_replace):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob(".sync-*.tmp")), [])
        self.assertEqual(len(list(self.store.backups.glob("*.json"))), 1)

    def test_new_view_uses_same_id_for_incubation_and_escapes_untrusted_labels(self):
        saved = self.create()
        data = self.store.read()
        key = saved["record_id"]
        data["records"][key]["name"] = "</script><img src=x onerror=alert(1)>"
        data["migration"] = {"selection_map": {"incub": {"n1": key}}}
        dest = self.archive / "view.html"
        receipt = render(data, dest)
        html = dest.read_text(encoding="utf-8")
        self.assertEqual(receipt["incubation"], 1)
        embedded = json.loads(re.search(r"const D=(.*?), storageKey=", html).group(1))
        self.assertEqual(embedded["incubation_ids"], [key])
        self.assertEqual(embedded["records"][0]["id"], key)
        self.assertNotIn("</script><img", html)
        self.assertIn("Object.values(choices)", html)

    @unittest.skipUnless(shutil.which("node"), "Optional Node runtime verifies actual browser handlers")
    def test_actual_browser_handlers_export_incubation_and_full_view_as_one_identity(self):
        saved = self.create()
        data = self.store.read()
        data["migration"] = {"selection_map": {"incub": {"n1": saved["record_id"]}}}
        path = self.archive / "interactive.html"
        render(data, path)
        result = subprocess.run(["node", str(ROOT / "tests" / "view_export.js"), str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

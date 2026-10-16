"""First connection/install acceptance uses only disposable synthetic directories."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.core import canonical, empty_store, Store
from scripts.bootstrap import FILES
from scripts.privacy_check import inspect_text


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-bootstrap-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.workspace = self.home / "workspace"
        self.archive = self.workspace / "archive"
        (self.archive / "logs").mkdir(parents=True)
        self.datafile = self.archive / "logs" / "s7-library.json"
        self.datafile.write_text(canonical(empty_store()), encoding="utf-8")
        self.skills = self.home / "skills"
        self.skills.mkdir()
        self.config = self.home / "connection.json"
        self.environment = dict(os.environ)
        self.environment.pop("PROJECT_MEMORY_ARCHIVE", None)
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"

    def run_tool(self, *args, root=ROOT, cli=False, valid=True):
        script = "memory_sync.py" if cli else "bootstrap.py"
        process = subprocess.run([sys.executable, "-B", "-X", "utf8", str(root / "scripts" / script), *map(str, args)],
                                 capture_output=True, encoding="utf-8", env=self.environment)
        self.assertEqual(process.returncode, 0 if valid else 2, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "")
        result = json.loads(process.stdout)
        self.assertLess(len(process.stdout), 4000)
        return result

    def setup_connection(self, *args):
        return self.run_tool("setup", "--workspace", self.workspace, "--archive", self.archive,
                             "--config", self.config, *args)

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in root.rglob("*") if p.is_file()}

    def test_first_setup_preview_apply_and_repeat_do_not_touch_archive(self):
        before = self.snapshot(self.workspace)
        self.assertEqual(self.setup_connection()["mode"], "plan")
        self.assertFalse(self.config.exists())
        self.assertEqual(self.setup_connection("--apply")["mode"], "created")
        config_before = self.config.read_bytes(), self.config.stat().st_mtime_ns
        self.assertEqual(self.setup_connection("--apply")["mode"], "unchanged")
        self.assertEqual(config_before, (self.config.read_bytes(), self.config.stat().st_mtime_ns))
        self.assertEqual(before, self.snapshot(self.workspace))

    def test_doctor_short_identity_only_read_only_no_lock_or_path_leak(self):
        self.setup_connection("--apply")
        before = self.snapshot(self.home)
        result = self.run_tool("doctor", "--config", self.config)
        self.assertEqual(result["store_id"], json.loads(self.datafile.read_text())["store_id"])
        self.assertEqual(result["access"], "read_only")
        self.assertEqual(result["write_probe"], "not_performed")
        self.assertEqual(result["client_discovery"], "not_tested")
        self.assertNotIn(str(self.home), json.dumps(result))
        self.assertEqual(before, self.snapshot(self.home))

    def test_missing_fields_are_reported_together_without_files(self):
        before = self.snapshot(self.home)
        result = self.run_tool("doctor", "--config", self.config, valid=False)
        self.assertEqual(result["error"], "MISSING: workspace,archive")
        self.assertEqual(before, self.snapshot(self.home))

    def test_missing_legacy_invalid_archive_and_invalid_config_never_initialize(self):
        self.datafile.unlink()
        before = self.snapshot(self.home)
        self.setup_connection_invalid()
        self.assertEqual(before, self.snapshot(self.home))
        for raw in ('{}', '{"schema_version":2}', 'not json'):
            self.datafile.write_text(raw)
            before = self.snapshot(self.home)
            result = self.setup_connection_invalid()
            self.assertEqual(result["error"], "READ_FAILED" if raw == "not json" else "MIGRATION_REQUIRED")
            self.assertEqual(before, self.snapshot(self.home))
        self.config.write_text('[]')
        self.run_tool("doctor", "--config", self.config, valid=False)

    def setup_connection_invalid(self, *extra):
        return self.run_tool("setup", "--workspace", self.workspace, "--archive", self.archive,
                             "--config", self.config, *extra, valid=False)

    def test_path_scope_relative_and_expected_store_id_fail(self):
        for options in (("--workspace", self.skills), ("--archive", "relative"),
                        ("--store-id", "00000000-0000-4000-8000-000000000999")):
            self.setup_connection_invalid(*options)
        self.assertFalse(self.config.exists())

    def test_existing_config_conflicts_and_identity_drift_preserve_content(self):
        self.setup_connection("--apply")
        before = self.config.read_bytes(), self.config.stat().st_mtime_ns
        self.setup_connection_invalid("--access", "read_write", "--apply")
        result = self.run_tool("doctor", "--config", self.config, "--access", "read_write", valid=False)
        self.assertIn("ACCESS_OVERRIDE_CONFLICT", result["error"])
        self.assertEqual(before, (self.config.read_bytes(), self.config.stat().st_mtime_ns))
        data = json.loads(self.datafile.read_text())
        data["store_id"] = "00000000-0000-4000-8000-000000000999"
        self.datafile.write_text(canonical(data))
        self.run_tool("doctor", "--config", self.config, valid=False)
        self.run_tool("--config", self.config, "status", cli=True, valid=False)

    def test_read_only_connection_rejects_actual_cli_writes_and_legacy_lock_preview(self):
        self.setup_connection("--apply")
        before = self.snapshot(self.home)
        for command in (("apply", "--file", self.home / "missing.json"),
                        ("migrate",), ("import", "--file", self.home / "missing.json",
                         "--expected-revision", "0", "--authorization-file", self.home / "missing-auth.json")):
            result = self.run_tool("--config", self.config, *command, cli=True, valid=False)
            self.assertIn("CONNECTION_READ_ONLY", result["error"])
        self.assertEqual(before, self.snapshot(self.home))

    def test_read_write_connection_apply_and_idempotent_readback(self):
        self.setup_connection("--access", "read_write", "--apply")
        request = self.home / "request.json"
        request.write_text(json.dumps({"request_id": "synthetic-bootstrap-create", "action": "create",
            "text": "Synthetic local connection", "record": {"name": "Synthetic herb reminder"},
            "authorization": {"basis": "explicit_user", "reference": "synthetic:user", "quote": "Save this synthetic idea"},
            "source": {"ref": "synthetic:user", "quote": "Synthetic herb reminder", "evidence_status": "verified"}}))
        first = self.run_tool("--config", self.config, "apply", "--file", request, cli=True)
        before = self.datafile.read_bytes()
        again = self.run_tool("--config", self.config, "apply", "--file", request, cli=True)
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["record_id"], again["record_id"])
        self.assertEqual(before, self.datafile.read_bytes())
        found = self.run_tool("--config", self.config, "search", "--query", "herb", "--limit", "5", cli=True)
        self.assertEqual(first["record_id"], found["results"][0]["id"])

    def test_explicit_archive_legacy_config_and_environment_remain_compatible(self):
        self.run_tool("--archive", self.archive, "status", cli=True)
        self.config.write_text(json.dumps({"archive": str(self.archive)}))
        self.run_tool("--config", self.config, "status", cli=True)
        self.environment["PROJECT_MEMORY_ARCHIVE"] = str(self.archive)
        self.run_tool("status", cli=True)

    def test_install_preview_apply_repeat_and_installed_default_config_discovery(self):
        args = ("install", "--skills-root", self.skills)
        self.assertEqual(self.run_tool(*args)["mode"], "plan")
        target = self.skills / "project-memory-sync"
        self.assertFalse(target.exists())
        self.run_tool(*args, "--apply")
        self.run_tool("setup", "--workspace", self.workspace, "--archive", self.archive, "--apply", root=target)
        before = self.snapshot(target)
        self.assertEqual(self.run_tool(*args, "--apply")["mode"], "unchanged")
        self.run_tool("doctor", root=target)
        self.run_tool("status", root=target, cli=True)
        self.assertEqual(before, self.snapshot(target))

    def test_install_protects_extra_assets_and_modified_code(self):
        self.run_tool("install", "--skills-root", self.skills, "--apply")
        target = self.skills / "project-memory-sync"
        asset = target / "user-note.txt"
        asset.write_bytes(b"preserve")
        before = self.snapshot(target)
        self.run_tool("install", "--skills-root", self.skills, "--apply", valid=False)
        self.assertEqual(before, self.snapshot(target))
        asset.unlink()
        (target / "SKILL.md").write_bytes(b"user changed")
        before = self.snapshot(target)
        self.run_tool("install", "--skills-root", self.skills, "--apply", valid=False)
        self.assertEqual(before, self.snapshot(target))

    def test_empty_existing_install_directory_is_protected(self):
        (self.skills / "project-memory-sync").mkdir()
        self.run_tool("install", "--skills-root", self.skills, "--apply", valid=False)

    def test_hardlinked_archive_rejected_without_changing_either_name(self):
        alias = self.home / "archive-alias.json"
        os.link(self.datafile, alias)
        before = self.snapshot(self.home)
        self.setup_connection_invalid("--apply")
        self.assertEqual(before, self.snapshot(self.home))

    def test_junction_or_symlink_scope_is_rejected(self):
        link = self.workspace / "linked-archive"
        self.directory_link(link, self.archive)
        self.setup_connection_invalid("--archive", link, "--apply")
        self.run_tool("install", "--skills-root", link, "--apply", valid=False)

    def directory_link(self, link, target):
        if os.name == "nt":
            process = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            self.assertEqual(process.returncode, 0)
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_existing_backup_junction_rejects_setup_and_post_setup_cli_without_external_write(self):
        self.setup_connection("--access", "read_write", "--apply")
        outside = self.home / "outside-workspace"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"preserve external bytes")
        self.directory_link(self.archive / "logs" / ".memory-sync-backups", outside)
        request = self.home / "junction-request.json"
        request.write_text(json.dumps({"request_id": "synthetic-junction-denial", "action": "create",
            "record": {"name": "Synthetic blocked reminder"},
            "authorization": {"basis": "explicit_user", "reference": "synthetic:user", "quote": "Save this synthetic idea"},
            "source": {"ref": "synthetic:user", "quote": "Synthetic blocked reminder", "evidence_status": "verified"}}))
        before = self.datafile.read_bytes(), self.snapshot(outside)
        self.run_tool("doctor", "--config", self.config, valid=False)
        self.run_tool("setup", "--workspace", self.workspace, "--archive", self.archive,
                      "--config", self.home / "other-config.json", "--access", "read_write", "--apply", valid=False)
        result = self.run_tool("--config", self.config, "apply", "--file", request, cli=True, valid=False)
        self.assertIn("LINK_OR_REPARSE_PATH_REJECTED", result["error"])
        self.assertEqual(before, (self.datafile.read_bytes(), self.snapshot(outside)))
        self.assertFalse(self.datafile.with_suffix(".json.lock").exists())

    def test_existing_empty_hardlinked_lock_rejects_connection_without_external_byte_write(self):
        external = self.home / "external-lock.txt"
        external.write_bytes(b"")
        lock = self.datafile.with_name(self.datafile.name + ".lock")
        os.link(external, lock)
        before = self.snapshot(self.home)
        result = self.setup_connection_invalid("--access", "read_write", "--apply")
        self.assertEqual(result["error"], "LOCK_MUST_BE_REGULAR_SINGLE_LINK_FILE")
        self.assertEqual(before, self.snapshot(self.home))

    def test_existing_hardlinked_backup_leaf_rejects_connection_without_modification(self):
        external = self.home / "external-backup.txt"
        external.write_bytes(b"preserve linked backup")
        backups = self.datafile.parent / ".memory-sync-backups"
        backups.mkdir()
        os.link(external, backups / ("a" * 64 + ".json"))
        before = self.snapshot(self.home)
        result = self.setup_connection_invalid("--access", "read_write", "--apply")
        self.assertEqual(result["error"], "BACKUP_MUST_BE_REGULAR_SINGLE_LINK_FILE")
        self.assertEqual(before, self.snapshot(self.home))

    def test_package_manifest_excludes_installed_private_config_and_scans_content(self):
        self.run_tool("install", "--skills-root", self.skills, "--apply")
        target = self.skills / "project-memory-sync"
        (target / "local-config.json").write_text('{"private_marker":"DO_NOT_SHIP_THIS"}')
        (target / "extra-private.txt").write_text("DO_NOT_SHIP_THIS")
        bundle = self.home / "bundle.zip"
        plan = self.run_tool("package", "--output", bundle, root=target)
        self.assertEqual(set(plan["files"]), set(FILES))
        self.assertFalse(bundle.exists())
        self.run_tool("package", "--output", bundle, "--apply", root=target)
        with zipfile.ZipFile(bundle) as archive:
            self.assertEqual(set(archive.namelist()), {"project-memory-sync/" + name for name in FILES})
            for name in archive.namelist():
                text = archive.read(name).decode("utf-8")
                self.assertNotIn("DO_NOT_SHIP_THIS", text)
                self.assertNotIn(str(self.home), text)
                self.assertEqual(inspect_text(text, name), [])
        before = bundle.read_bytes()
        self.run_tool("package", "--output", bundle, "--apply", root=target, valid=False)
        self.assertEqual(before, bundle.read_bytes())


if __name__ == "__main__":
    unittest.main()

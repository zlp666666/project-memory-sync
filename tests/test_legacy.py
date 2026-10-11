"""Migration tests use isolated synthetic archives only."""
import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest
import uuid


MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "legacy.py"
SPEC = importlib.util.spec_from_file_location("migration_under_test", MODULE)
legacy_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy_module)
build_migration = legacy_module.build_migration
MigrationError = legacy_module.MigrationError


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = pathlib.Path(self.temp.name)
        (self.archive / "batches").mkdir()

    def write_batch(self, filename, rows):
        (self.archive / "batches" / filename).write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    def fixture(self):
        one = {"name": "Garden sensor", "depth": "深入探索", "status": "暂时搁置",
               "proj": "Incorrect old attachment", "origin": "用户提出", "w": "Only an idea"}
        two = {"name": "Garden camera", "depth": "灵感", "status": "暂时搁置", "w": "One sentence"}
        return {"user_lib": [one, two], "ai_zone": [], "qa_zone": [],
                "incub": [{**one, "incub_rank": 1, "incub_score": 9}]}

    def row(self, uid, work, name="Garden sensor", origin="user"):
        return {"uid": uid, "source": "synthetic", "start": "2026-01-01T12:00:00Z", "status": "done",
                "projects": [{"name": "Garden", "relation": "main"}, {"name": "Camera", "relation": "task"}],
                "ideas": [{"name": name, "originator": origin, "work_done": work, "stop_reason": ""}]}

    def test_preserves_legacy_and_quarantines_inferences(self):
        old = self.fixture()
        snapshot = copy.deepcopy(old)
        result = build_migration(self.archive, old)
        self.assertEqual(old, snapshot)
        self.assertEqual(result["legacy_data"], snapshot)
        self.assertEqual(result["schema_version"], 3)
        uuid.UUID(result["store_id"])
        for record in result["records"].values():
            self.assertEqual(record["depth"], "unknown")
            self.assertEqual(record["intent"], "undecided")
            self.assertNotIn("project_id", record)
            self.assertEqual(record["origin"], "uncertain")
        incub = result["migration"]["selection_map"]["incub"]["n1"]
        self.assertEqual(incub, result["migration"]["selection_map"]["user_lib"]["u1"])
        self.assertEqual(result["migration"]["audit"]["incubation_links"], 1)

    def test_ids_do_not_depend_on_legacy_order_or_sort(self):
        old = self.fixture()
        first = build_migration(self.archive, old)
        old["user_lib"].reverse()
        second = build_migration(self.archive, old)
        self.assertEqual(set(first["records"]), set(second["records"]))
        self.assertEqual({event["id"] for event in first["events"]}, {event["id"] for event in second["events"]})

    def test_full_names_and_full_payloads_keep_distinct_identities(self):
        common = "A" * 50
        old = {"user_lib": [{"name": common + " first", "w": "nothing"},
                            {"name": common + " second", "w": "nothing"},
                            {"name": common + " first", "w": "later attempt"}], "incub": []}
        result = build_migration(self.archive, old)
        self.assertEqual(len(result["records"]), 3)
        self.assertEqual(result["migration"]["audit"]["automatic_name_merges"], 0)

    def test_repeated_source_is_idempotent_and_all_refs_are_retained(self):
        row = self.row("session-1", "Research")
        self.write_batch("a.jsonl", [row])
        first = build_migration(self.archive, {})
        self.write_batch("b.jsonl", [row])
        second = build_migration(self.archive, {})
        self.assertEqual(set(first["records"]), set(second["records"]))
        self.assertEqual(len(second["events"]), 1)
        self.assertEqual(second["events"][0]["source"]["refs"], ["batches/a.jsonl:1", "batches/b.jsonl:1"])

    def test_duplicate_extraction_keeps_conflicting_context_claims(self):
        first = self.row("session-1", "Research")
        second = copy.deepcopy(first)
        second["projects"] = [{"name": "Different extractor attachment"}]
        second["quality_flags"] = "alternative model classification"
        self.write_batch("a.jsonl", [first])
        self.write_batch("b.jsonl", [second])
        result = build_migration(self.archive, {})
        self.assertEqual(len(result["records"]), 1)
        variants = result["events"][0]["source"]["context_variants"]
        self.assertEqual(len(variants), 2)
        self.assertNotEqual(variants[0]["projects"], variants[1]["projects"])
        self.assertEqual(variants[1]["quality_flags"], "alternative model classification")

    def test_same_name_keeps_every_different_experience(self):
        rows = [self.row("session-1", "research"), self.row("session-2", "developed"),
                self.row("session-3", "failed"), self.row("session-4", "shelved")]
        self.write_batch("history.jsonl", rows)
        result = build_migration(self.archive, {})
        self.assertEqual(len(result["records"]), 4)
        self.assertEqual(len(result["events"]), 4)
        self.assertEqual({event["source"]["extracted_idea"]["work_done"] for event in result["events"]},
                         {"research", "developed", "failed", "shelved"})
        self.assertTrue(all(event["source"]["quote"] == "" for event in result["events"]))
        self.assertTrue(all(event["source"]["quote_kind"] == "extracted_summary" for event in result["events"]))

    def test_context_does_not_duplicate_into_every_project(self):
        self.write_batch("history.jsonl", [self.row("session-1", "One idea", origin="ai")])
        result = build_migration(self.archive, {})
        self.assertEqual(len(result["records"]), 1)
        record = next(iter(result["records"].values()))
        self.assertEqual(record["origin"], "ai_suggested")
        self.assertEqual(record["unverified_project_mentions"], ["Garden", "Camera"])
        self.assertNotIn("project_id", record)

    def test_every_idea_imported_not_only_first_six(self):
        row = self.row("session-1", "Research")
        row["ideas"] = [{"name": "Synthetic idea " + str(number), "originator": "user"} for number in range(12)]
        self.write_batch("history.jsonl", [row])
        result = build_migration(self.archive, {})
        self.assertEqual(result["migration"]["audit"]["independent_idea_occurrences"], 12)

    def test_old_misalignment_is_documented_and_not_auto_applied(self):
        old = self.fixture()
        old["user_lib"][0]["depth"] = "灵感"
        old["user_lib"][1]["depth"] = "原型尝试"
        old["incub"][0]["depth"] = "灵感"
        result = build_migration(self.archive, old)
        mapping = result["migration"]["selection_map"]
        self.assertNotEqual(mapping["user_lib"]["u1"], mapping["user_lib_visible"]["u1"])
        self.assertTrue(result["migration"]["unsafe_legacy_decisions"]["require_review"])
        self.assertEqual(result["migration"]["audit"]["legacy_selection_mismatches"], 2)

    def test_historical_project_aliases_are_available_without_formal_card(self):
        summary = {"name": "Garden", "aliases": ["Plant lab", "Garden"], "stage": {"done": 2}, "parents": []}
        (self.archive / "project-index.json").write_text(json.dumps({"projects": {"garden": summary}}), encoding="utf-8")
        result = build_migration(self.archive, {})
        record = next(iter(result["records"].values()))
        self.assertEqual(record["kind"], "project")
        self.assertEqual(record["aliases"], ["Garden", "Plant lab"])
        self.assertFalse(record["formal_project_card"])
        self.assertEqual(record["progress"], "unknown")

    def test_inventory_supplies_only_missing_date_and_client(self):
        (self.archive / "inventory.csv").write_text("uid,source,start,path\nsession-1,synthetic,2026-01-02,ignored\n", encoding="utf-8")
        row = self.row("session-1", "Research")
        del row["start"]
        del row["source"]
        self.write_batch("history.jsonl", [row])
        result = build_migration(self.archive, {})
        self.assertEqual(result["events"][0]["occurred_at"], "2026-01-02")
        self.assertEqual(result["events"][0]["source"]["client"], "synthetic")
        self.assertNotIn("ignored", json.dumps(result))

    def test_bad_batch_fails_without_writing_any_file(self):
        path = self.archive / "batches" / "broken.jsonl"
        path.write_text('{"uid":', encoding="utf-8")
        before = {str(item): item.read_bytes() for item in self.archive.rglob("*") if item.is_file()}
        with self.assertRaises(MigrationError):
            build_migration(self.archive, self.fixture())
        after = {str(item): item.read_bytes() for item in self.archive.rglob("*") if item.is_file()}
        self.assertEqual(before, after)

    def test_rejects_already_migrated_and_unmatched_incubation(self):
        with self.assertRaises(MigrationError):
            build_migration(self.archive, {"schema_version": 3})
        with self.assertRaises(MigrationError):
            build_migration(self.archive, {"user_lib": [], "incub": [{"name": "Missing library record"}]})

    def test_snapshot_passes_core_validation(self):
        core_path = MODULE.with_name("core.py")
        if not core_path.exists():
            self.skipTest("Core integration module is not present")
        core_spec = importlib.util.spec_from_file_location("core_validation_under_test", core_path)
        core = importlib.util.module_from_spec(core_spec)
        core_spec.loader.exec_module(core)
        self.write_batch("history.jsonl", [self.row("session-1", "Research")])
        snapshot = build_migration(self.archive, self.fixture())
        self.assertIs(core.validate(snapshot), snapshot)

    def test_relative_source_fingerprints_and_postmigration_legacy_refs(self):
        self.write_batch("history.jsonl", [self.row("session-1", "Research")])
        snapshot = build_migration(self.archive, self.fixture())
        fingerprints = snapshot["migration"]["source_files_sha256"]
        self.assertEqual(set(fingerprints), {"batches/history.jsonl"})
        self.assertEqual(len(fingerprints["batches/history.jsonl"]), 64)
        self.assertIn("Missing records are not evidence", snapshot["migration"]["coverage"])
        refs = [event["source"]["ref"] for event in snapshot["events"]]
        self.assertTrue(any("#legacy_data/user_lib:" in ref for ref in refs))


if __name__ == "__main__":
    unittest.main()

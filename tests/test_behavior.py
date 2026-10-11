"""Independent A-I acceptance and failure tests; all data is synthetic.

Runtime archives are created beside the repository under runtime/, never in the
authoritative logs/ directory. No client installation or network is performed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
import core
from core import Store, SyncError, atomic_write, canonical, empty_store, history, search, validate


AUTH = {"basis": "explicit_user", "reference": "synthetic:user-message-1",
        "quote": "Save this synthetic idea and its explicitly requested updates."}
SOURCE = {"ref": "synthetic:session-1#message-1", "quote": "A one sentence idea, with no work done.",
          "evidence_status": "verified"}


def operation(action, request_id, **fields):
    return {"action": action, "request_id": request_id,
            "authorization": copy.deepcopy(AUTH), "source": copy.deepcopy(SOURCE),
            "actor": "synthetic-agent", **fields}


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        runtime = REPO.parent / "runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="acceptance-", dir=runtime)
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name)
        self.path = self.archive / "logs" / "s7-library.json"
        atomic_write(self.path, (canonical(empty_store()) + "\n").encode("utf-8"))
        self.store = Store(self.path)

    def create(self, name="Synthetic garden sensor", request_id="create-1", **record):
        return self.store.apply(operation("create", request_id, record={"name": name, **record}))

    def seed_project(self):
        data = self.store.read()
        key = str(uuid.uuid4())
        data["records"][key] = {"id": key, "kind": "project", "name": "Synthetic garden",
                                "aliases": ["Synthetic plant lab"], "origin": "user_proposed",
                                "depth": "prototype", "progress": "developed", "intent": "keep",
                                "evidence_status": "verified"}
        atomic_write(self.path, (canonical(data) + "\n").encode("utf-8"))
        return key

    def cli(self, *args, check=True):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPTS / "memory_sync.py"),
                                 "--archive", str(self.archive), *args],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        payload = json.loads(result.stdout)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return payload

    def write_json(self, filename, data):
        path = self.archive / filename
        path.write_text(canonical(data), encoding="utf-8")
        return path

    def assert_rejected_without_mutation(self, payload, error):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SyncError, error):
            self.store.apply(payload)
        self.assertEqual(self.path.read_bytes(), before)

    def test_A_one_sentence_without_results_is_saved_with_quote_and_receipt(self):
        result = self.create("给阳台花盆做一个缺水提醒")
        record = self.store.read()["records"][result["record_id"]]
        uuid.UUID(result["record_id"])
        self.assertEqual(record["depth"], "idea")
        self.assertEqual(record["progress"], "unknown")
        self.assertEqual(record["intent"], "undecided")
        self.assertEqual(record["origin"], "user_proposed")
        self.assertEqual(Path(result["saved_path"]), self.path.resolve())
        self.assertEqual(result["actual_update"]["source"]["quote"], SOURCE["quote"])
        self.assertEqual(result["actual_update"]["changes"]["created_record"], record)

    def test_B_second_discussion_appends_without_creating_another_object(self):
        key = self.create()["record_id"]
        update = operation("append", "discussion-2", record_id=key, text="Compare inexpensive sensors.",
                           event_type="research", changes={"depth": "research", "progress": "researched"})
        update["source"] = {"ref": "synthetic:session-2#message-2", "quote": "Research inexpensive sensors.",
                            "evidence_status": "verified"}
        self.store.apply(update)
        data = self.store.read()
        self.assertEqual(len(data["records"]), 1)
        self.assertEqual(len(history(data, key)["events"]), 2)
        self.assertEqual(history(data, key)["events"][0]["source"]["quote"], SOURCE["quote"])
        self.assertEqual(data["records"][key]["progress"], "researched")

    def test_C_feature_is_attached_to_existing_project_not_a_new_project(self):
        key = self.seed_project()
        result = self.store.apply(operation("append", "feature-1", record_id=key,
                                            text="Add a low water reminder to the existing project.",
                                            event_type="feature", details={"feature": "low-water reminder"}))
        self.assertEqual(result["record_id"], key)
        self.assertEqual(len(self.store.read()["records"]), 1)
        self.assertEqual(history(self.store.read(), key)["events"][0]["details"]["feature"], "low-water reminder")
        self.assert_rejected_without_mutation(operation("create", "formal-project", record={"name": "New project", "kind": "project"}),
                                              "NO_FORMAL_PROJECT_CREATION")

    def test_D_AI_suggestion_cannot_become_a_committed_user_project(self):
        key = self.create("AI suggested sensor", origin="ai_suggested")["record_id"]
        self.assertEqual(self.store.read()["records"][key]["origin"], "ai_suggested")
        self.assert_rejected_without_mutation(operation("decision", "ai-restart", record_id=key,
                                              text="Restart", changes={"intent": "restart"}), "UNCONFIRMED_INTENT")
        self.assert_rejected_without_mutation(operation("append", "ai-user", record_id=key,
                                              text="Pretend user approved", changes={"origin": "user_approved"}),
                                              "USER_APPROVAL_QUOTE_REQUIRED")
        result = self.store.apply(operation("decision", "user-approves", record_id=key,
                                            text="The user now approves exploring it.", approval_quote="I approve researching this idea.",
                                            changes={"origin": "user_approved", "intent": "keep"}))
        self.assertEqual(result["record_id"], key)
        self.assertEqual(self.store.read()["records"][key]["origin"], "user_approved")

    def test_D_knowledge_and_uncertain_records_are_distinct_from_user_ideas(self):
        for origin in ("knowledge_qa", "uncertain"):
            result = self.create("Synthetic " + origin, request_id="create-" + origin, origin=origin,
                                 evidence_status="unverified")
            record = self.store.read()["records"][result["record_id"]]
            self.assertEqual(record["origin"], origin)
            self.assertEqual(record["intent"], "undecided")
            self.assertEqual(record["evidence_status"], "unverified")
        self.assert_rejected_without_mutation(operation("create", "fake-approved", record={"name": "Fake approval", "origin": "user_approved"}),
                                              "USER_APPROVAL_QUOTE_REQUIRED")

    def test_E_research_development_failure_and_pause_can_all_be_replayed(self):
        key = self.create()["record_id"]
        phases = [("researched", "research", "2026-02-01T09:00:00Z", "Compared sensor options"),
                  ("developed", "prototype", "2026-03-01T09:00:00Z", "Built a prototype"),
                  ("failed", "prototype", "2026-04-01T09:00:00Z", "The sensor rusted in a wet pot"),
                  ("paused", "prototype", "2026-05-01T09:00:00Z", "Paused because the sensor was unreliable")]
        # Submit in an order different from the actual event dates.
        for index in (2, 0, 1, 3):
            progress, depth, occurred_at, text = phases[index]
            self.store.apply(operation("append", "phase-" + str(index), record_id=key, text=text,
                                       occurred_at=occurred_at, event_type=progress,
                                       changes={"progress": progress, "depth": depth},
                                       source={"ref": "synthetic:phase-" + str(index), "quote": text,
                                               "evidence_status": "verified"}))
        result = history(self.store.read(), key)
        actual = [event["text"] for event in result["events"] if event["kind"] == "append"]
        self.assertEqual(actual, [phase[3] for phase in phases])
        self.assertEqual(result["record"]["progress"], "paused")
        self.assertEqual(result["record"]["intent"], "undecided")
        self.assertIn("absence is not proof", result["coverage"])
        self.assertTrue(all(event["source"]["ref"].startswith("synthetic:") for event in result["events"]))

    def test_F_similar_names_and_names_with_the_same_first_40_chars_are_distinct(self):
        first = self.create("Synthetic garden sensor", request_id="name-1")["record_id"]
        second = self.create("Synthetic garden camera", request_id="name-2")["record_id"]
        prefix = "同一个名称前缀" * 10
        third = self.create(prefix + "温度", request_id="name-3")["record_id"]
        fourth = self.create(prefix + "湿度", request_id="name-4")["record_id"]
        self.assertEqual(len({first, second, third, fourth}), 4)
        self.assertEqual(len(self.store.read()["redirects"]), 0)
        self.assertTrue(all(not result["automatic_merge"] for result in search(self.store.read(), "Synthetic garden")))

    def test_F_exact_name_duplicate_requires_explicit_distinct_confirmation(self):
        first = self.create()["record_id"]
        self.assert_rejected_without_mutation(operation("create", "same-name", record={"name": "Synthetic garden sensor"}),
                                              "DUPLICATE_CANDIDATE")
        second = self.store.apply(operation("create", "distinct-name", record={"name": "Synthetic garden sensor"},
                                           confirmed_distinct=True, confirmation_quote="These are two separate sensors."))["record_id"]
        self.assertNotEqual(first, second)

    def test_F_manual_merge_retains_original_ids_history_quotes_and_alias_search(self):
        first = self.create("Synthetic garden sensor", request_id="merge-source")["record_id"]
        second = self.create("Synthetic plant monitor", request_id="merge-target")["record_id"]
        self.store.apply(operation("append", "merge-experience", record_id=first, text="Sensor failed in wet soil."))
        self.assert_rejected_without_mutation(operation("merge", "unsafe-merge", source_id=first, target_id=second),
                                              "MANUAL_MERGE_CONFIRMATION_REQUIRED")
        self.store.apply(operation("merge", "safe-merge", source_id=first, target_id=second,
                                   confirmed_same_object=True, confirmation_quote="Both names mean the same device."))
        data = self.store.read()
        self.assertIn(first, data["records"])
        self.assertEqual(data["redirects"][first], second)
        result = history(data, first)
        self.assertEqual(result["record"]["id"], second)
        self.assertEqual(set(result["associated_ids"]), {first, second})
        self.assertEqual(len(result["events"]), 4)
        self.assertIn("Sensor failed in wet soil.", [event["text"] for event in result["events"]])
        found = search(data, "Synthetic garden sensor")
        self.assertEqual(found[0]["id"], second)
        self.assertEqual(found[0]["match"], "exact")

    def test_G_alias_and_map_order_do_not_change_stable_identity(self):
        key = self.create()["record_id"]
        self.create("Synthetic other idea", request_id="other-record")
        self.store.apply(operation("alias", "alias-1", record_id=key, alias="Synthetic plant alarm"))
        data = self.store.read()
        data["records"] = dict(reversed(list(data["records"].items())))
        atomic_write(self.path, canonical(data).encode("utf-8"))
        self.assertEqual(search(self.store.read(), "Synthetic plant alarm")[0]["id"], key)
        self.assertEqual(history(self.store.read(), key)["record"]["id"], key)

    def test_G_repeat_request_is_noop_and_changed_payload_is_rejected(self):
        payload = operation("create", "idempotent-1", record={"name": "Synthetic retry"})
        first = self.store.apply(payload)
        before = self.path.read_bytes()
        second = self.store.apply(copy.deepcopy(payload))
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(self.path.read_bytes(), before)
        payload["record"]["name"] = "Different synthetic object"
        self.assert_rejected_without_mutation(payload, "IDEMPOTENCY_CONFLICT")

    def test_G_export_import_roundtrip_and_additive_repeated_import(self):
        key = self.create()["record_id"]
        output = self.archive / "synthetic-export.json"
        receipt = self.cli("export", "--output", str(output))
        exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported, self.store.read())
        self.assertEqual(receipt["sha256"], core.digest(exported))
        self.assertTrue(self.store.import_document(exported, exported["revision"], AUTH)["duplicate"])
        branch_path = self.archive / "branch" / "logs" / "s7-library.json"
        atomic_write(branch_path, output.read_bytes())
        branch = Store(branch_path)
        branch.apply(operation("append", "branch-experience", record_id=key, text="An additional synthetic discussion."))
        incoming = branch.read()
        imported = self.store.import_document(incoming, self.store.read()["revision"], AUTH)
        self.assertEqual(imported["new_events"], 1)
        self.assertIn("An additional synthetic discussion.", [event["text"] for event in history(self.store.read(), key)["events"]])
        before = self.path.read_bytes()
        repeated = self.store.import_document(incoming, self.store.read()["revision"], AUTH)
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_G_divergent_import_does_not_overwrite_local_evidence(self):
        key = self.create()["record_id"]
        incoming = self.store.read()
        incoming["records"][key]["name"] = "A conflicting imported name"
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SyncError, "IMPORT_CONFLICT"):
            self.store.import_document(incoming, incoming["revision"], AUTH)
        self.assertEqual(self.path.read_bytes(), before)
        other_store = empty_store()
        with self.assertRaisesRegex(SyncError, "STORE_ID_MISMATCH"):
            self.store.import_document(other_store, incoming["revision"], AUTH)

    def test_G_rollback_restores_state_and_retains_what_happened(self):
        key = self.create()["record_id"]
        self.store.apply(operation("append", "bad-status", record_id=key, text="An erroneous status update.",
                                   changes={"progress": "completed"}))
        result = self.store.apply(operation("rollback", "rollback-1", target_request_id="bad-status", expected_revision=2))
        data = self.store.read()
        self.assertEqual(data["records"][key]["progress"], "unknown")
        self.assertEqual(data["operations"]["bad-status"]["status"], "reverted")
        self.assertEqual(result["record_id"], key)
        self.assertEqual(len(history(data, key)["events"]), 3)
        self.assertIn("An erroneous status update.", [event["text"] for event in data["events"]])

    def test_G_rollback_cannot_discard_later_work(self):
        key = self.create()["record_id"]
        self.store.apply(operation("append", "latest-update", record_id=key, text="A later discussion."))
        self.assert_rejected_without_mutation(operation("rollback", "discard-later", target_request_id="create-1", expected_revision=2),
                                              "ROLLBACK_ONLY_LATEST")
        self.assert_rejected_without_mutation(operation("rollback", "missing-revision", target_request_id="latest-update"),
                                              "ROLLBACK_REVISION_REQUIRED")

    def test_G_backup_is_content_addressed_and_can_reconstruct_before_state(self):
        before = self.path.read_bytes()
        result = self.create()
        backup = self.store.backups / (result["backup_sha256"] + ".json")
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), result["backup_sha256"])
        self.assertEqual(validate(json.loads(backup.read_text(encoding="utf-8")))["revision"], 0)
        self.assertTrue(self.cli("verify-backup", "--sha256", result["backup_sha256"])["restorable_json"])

    def test_H_two_processes_append_without_lost_updates_or_corrupt_reads(self):
        key = self.create()["record_id"]
        barrier = self.archive / "start.signal"
        worker = """import json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from core import Store
jobs=json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8'))
while not pathlib.Path(sys.argv[4]).exists(): time.sleep(.01)
store=Store(sys.argv[2])
for job in jobs: store.apply(job)
print('completed')
"""
        jobs = []
        for actor in ("agent-1", "agent-2"):
            tasks = [operation("append", actor + "-" + str(i), record_id=key, actor=actor,
                               text=actor + " synthetic update " + str(i)) for i in range(8)]
            jobs.append(self.write_json(actor + ".json", tasks))
        processes = [subprocess.Popen([sys.executable, "-c", worker, str(SCRIPTS), str(self.path), str(job), str(barrier)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8") for job in jobs]
        self.addCleanup(lambda: [proc.kill() for proc in processes if proc.poll() is None])
        barrier.touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        data = self.store.read()
        self.assertEqual(data["revision"], 17)
        self.assertEqual(len(data["records"]), 1)
        self.assertEqual(len(data["events"]), 17)
        self.assertEqual(len({event["id"] for event in data["events"]}), 17)
        self.assertEqual({event["request_id"] for event in data["events"] if event["kind"] == "append"},
                         {"agent-" + str(agent) + "-" + str(i) for agent in (1, 2) for i in range(8)})

    def test_H_stale_revision_is_rejected_without_losing_current_update(self):
        key = self.create()["record_id"]
        self.store.apply(operation("append", "fresh-update", record_id=key, text="Fresh update", expected_revision=1))
        self.assert_rejected_without_mutation(operation("append", "stale-update", record_id=key, text="Stale update", expected_revision=1),
                                              "REVISION_CONFLICT")

    def test_I_fresh_process_can_find_alias_and_replay_source_evidence(self):
        key = self.create("Synthetic archived pot monitor")["record_id"]
        self.store.apply(operation("alias", "session-alias", record_id=key, alias="旧花盆监测构想"))
        self.store.apply(operation("append", "session-reason", record_id=key,
                                   text="Paused because long term waterproofing was untested.",
                                   changes={"progress": "paused"},
                                   source={"ref": "synthetic:old-session#7", "quote": "Pause until waterproofing is reliable.",
                                           "evidence_status": "unverified"}))
        found = self.cli("search", "--query", "旧花盆监测构想")
        self.assertEqual(found["results"][0]["id"], key)
        result = self.cli("history", "--id", key)
        self.assertEqual(result["record"]["progress"], "paused")
        self.assertEqual(result["record"]["intent"], "undecided")
        reason = [event for event in result["events"] if event["request_id"] == "session-reason"][0]
        self.assertEqual(reason["source"]["ref"], "synthetic:old-session#7")
        self.assertEqual(reason["source"]["evidence_status"], "unverified")

    def test_authorization_and_source_are_required_and_invalid_state_is_atomic(self):
        key = self.create()["record_id"]
        payload = operation("append", "unauthorized", record_id=key, text="No permission")
        del payload["authorization"]
        self.assert_rejected_without_mutation(payload, "AUTHORIZATION_REQUIRED")
        payload = operation("append", "no-source", record_id=key, text="No source")
        del payload["source"]
        self.assert_rejected_without_mutation(payload, "SOURCE_REQUIRED")
        self.assert_rejected_without_mutation(operation("append", "invalid-state", record_id=key,
                                              text="Bad enum", changes={"progress": "made_up"}), "INVALID_STORE")
        self.assert_rejected_without_mutation(operation("append", "overwrite-name", record_id=key,
                                              text="Rename without preserving evidence", changes={"name": "Other"}), "UNSUPPORTED_CHANGE")
        self.assert_rejected_without_mutation(operation("append", "intent-without-decision", record_id=key,
                                              text="Restart", changes={"intent": "restart"}), "INTENT_REQUIRES_DECISION_ACTION")

    def test_approved_scope_cannot_update_a_record_outside_its_bounds(self):
        key = self.create()["record_id"]
        payload = operation("append", "outside-scope", record_id=key, text="No approved access")
        payload["authorization"] = {"basis": "approved_scope", "reference": "synthetic:scope",
                                    "quote": "Only another synthetic record", "actions": ["append"],
                                    "record_ids": [str(uuid.uuid4())]}
        self.assert_rejected_without_mutation(payload, "OUTSIDE_APPROVED_RECORDS")

    def test_approved_scope_cannot_rollback_a_record_outside_its_bounds(self):
        self.create()
        payload = operation("rollback", "outside-rollback-scope", target_request_id="create-1", expected_revision=1)
        payload["authorization"] = {"basis": "approved_scope", "reference": "synthetic:scope",
                                    "quote": "No record updates approved", "actions": ["rollback"], "record_ids": []}
        self.assert_rejected_without_mutation(payload, "OUTSIDE_APPROVED_RECORDS")

    def test_rollback_of_rollback_is_rejected_without_changing_operation_status(self):
        key = self.create()["record_id"]
        payload = operation("append", "original-update", record_id=key, text="Completed", changes={"progress": "completed"})
        self.store.apply(payload)
        self.store.apply(operation("rollback", "first-rollback", target_request_id="original-update", expected_revision=2))
        self.assert_rejected_without_mutation(operation("rollback", "undo-rollback", target_request_id="first-rollback", expected_revision=3),
                                              "ROLLBACK_OF_ROLLBACK_NOT_SUPPORTED")
        data = self.store.read()
        self.assertEqual(data["records"][key]["progress"], "unknown")
        self.assertEqual(data["operations"]["original-update"]["status"], "reverted")
        self.assertEqual(self.store.apply(payload)["operation_status"], "reverted")

    def test_rolled_back_create_is_hidden_from_lexical_and_semantic_search(self):
        key = self.create()["record_id"]
        self.store.apply(operation("rollback", "hide-create", target_request_id="create-1", expected_revision=1))
        data = self.store.read()
        self.assertEqual(search(data, "Synthetic garden sensor"), [])
        self.assertEqual(search(data, "Unrelated query", semantic_candidates=[{"id": key, "reason": "A synthetic semantic match"}]), [])
        self.assertEqual(history(data, key)["record"]["id"], key)

    def test_import_rejects_history_missing_source_quote_or_reference(self):
        self.create()
        incoming = self.store.read()
        malformed = copy.deepcopy(incoming["events"][0])
        malformed["id"] = str(uuid.uuid4())
        malformed["request_id"] = "malformed-history"
        malformed["source"].pop("quote")
        incoming["events"].append(malformed)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SyncError, "INVALID_STORE|SOURCE_REQUIRED"):
            self.store.import_document(incoming, incoming["revision"], AUTH)
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_authorization_and_source_types_raise_sync_error_without_writing(self):
        key = self.create()["record_id"]
        malformed = [
            ("authorization-array", "authorization", [], "AUTHORIZATION_REQUIRED"),
            ("authorization-basis-object", "authorization", {**AUTH, "basis": {}}, "AUTHORIZATION_REQUIRED"),
            ("authorization-reference-array", "authorization", {**AUTH, "reference": ["not text"]}, "AUTHORIZATION_REQUIRED"),
            ("authorization-quote-object", "authorization", {**AUTH, "quote": {"not": "text"}}, "AUTHORIZATION_REQUIRED"),
            ("authorization-whitespace", "authorization", {**AUTH, "quote": "  "}, "AUTHORIZATION_REQUIRED"),
            ("scope-actions-string", "authorization", {**AUTH, "basis": "approved_scope", "actions": "append", "record_ids": [key]}, "INVALID_APPROVED_SCOPE"),
            ("scope-records-object", "authorization", {**AUTH, "basis": "approved_scope", "actions": ["append"], "record_ids": {key: True}}, "INVALID_APPROVED_SCOPE"),
            ("scope-action-element-object", "authorization", {**AUTH, "basis": "approved_scope", "actions": [{}], "record_ids": [key]}, "INVALID_APPROVED_SCOPE"),
            ("source-array", "source", [], "SOURCE_REQUIRED"),
            ("source-reference-number", "source", {**SOURCE, "ref": 123}, "SOURCE_REQUIRED"),
            ("source-reference-blank", "source", {**SOURCE, "ref": " "}, "SOURCE_REQUIRED"),
            ("source-quote-array", "source", {**SOURCE, "quote": ["not text"]}, "SOURCE_REQUIRED"),
            ("source-evidence-array", "source", {**SOURCE, "evidence_status": []}, "EVIDENCE_STATUS_REQUIRED"),
        ]
        for name, field, value, error in malformed:
            with self.subTest(case=name):
                payload = operation("append", name, record_id=key, text="Synthetic malformed request")
                payload[field] = value
                self.assert_rejected_without_mutation(payload, error)

    def test_malformed_record_change_detail_and_event_types_do_not_write(self):
        key = self.create()["record_id"]
        malformed = [
            ("record-array", "create", "record", [], "RECORD_MUST_BE_OBJECT"),
            ("record-null", "create", "record", None, "RECORD_MUST_BE_OBJECT"),
            ("record-name-number", "create", "record", {"name": 123}, "EMPTY_QUERY"),
            ("record-aliases-string", "create", "record", {"name": "Synthetic malformed aliases", "aliases": "not a list"}, "INVALID_STORE"),
            ("changes-array", "append", "changes", [], "CHANGES_AND_DETAILS_MUST_BE_OBJECTS"),
            ("changes-null", "append", "changes", None, "CHANGES_AND_DETAILS_MUST_BE_OBJECTS"),
            ("details-array", "append", "details", [], "CHANGES_AND_DETAILS_MUST_BE_OBJECTS"),
            ("details-string", "append", "details", "not an object", "CHANGES_AND_DETAILS_MUST_BE_OBJECTS"),
            ("event-text-object", "append", "text", {"not": "text"}, "EVENT_TEXT_AND_TIME_MUST_BE_STRINGS"),
            ("event-time-number", "append", "occurred_at", 123, "EVENT_TEXT_AND_TIME_MUST_BE_STRINGS"),
            ("alias-object", "alias", "alias", {"not": "text"}, "ALIAS_REQUIRED"),
        ]
        for name, action, field, value, error in malformed:
            with self.subTest(case=name):
                payload = operation(action, name, text="Synthetic malformed request")
                if action != "create":
                    payload["record_id"] = key
                payload[field] = value
                self.assert_rejected_without_mutation(payload, error)

    def test_cli_malformed_json_returns_error_object_without_traceback_or_live_mutation(self):
        key = self.create()["record_id"]
        malformed = [
            [],
            operation("create", "cli-record-array", record=[]),
            operation("append", "cli-auth-array", record_id=key, text="Invalid", authorization=[]),
            operation("append", "cli-source-array", record_id=key, text="Invalid", source=[]),
            operation("append", "cli-changes-array", record_id=key, text="Invalid", changes=[]),
            operation("append", "cli-details-array", record_id=key, text="Invalid", details=[]),
        ]
        for index, payload in enumerate(malformed):
            with self.subTest(case=index):
                path = self.write_json("cli-malformed-" + str(index) + ".json", payload)
                before = self.path.read_bytes()
                result = self.cli("apply", "--file", str(path), check=False)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_type"], "SyncError")
                self.assertIsInstance(result["error"], str)
                self.assertEqual(self.path.read_bytes(), before)
        path = self.archive / "cli-invalid-json.json"
        path.write_text('{"action":', encoding="utf-8")
        before = self.path.read_bytes()
        result = self.cli("apply", "--file", str(path), check=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "JSONDecodeError")
        self.assertEqual(self.path.read_bytes(), before)

    def test_redirected_id_requires_scope_authorization_for_actual_target(self):
        source = self.create("Synthetic redirect source", request_id="redirect-source")["record_id"]
        target = self.create("Synthetic redirect target", request_id="redirect-target")["record_id"]
        self.store.apply(operation("merge", "redirect-merge", source_id=source, target_id=target,
                                   confirmed_same_object=True, confirmation_quote="These are the same synthetic object."))
        scope = {"basis": "approved_scope", "reference": "synthetic:redirect-scope", "quote": "Update only the approved records.",
                 "actions": ["append"], "record_ids": [source]}
        self.assert_rejected_without_mutation(operation("append", "redirect-outside-target", record_id=source,
                                              text="Cannot bypass target authorization via an old ID.", authorization=scope),
                                              "OUTSIDE_APPROVED_RECORDS")
        result = self.store.apply(operation("append", "redirect-authorized", record_id=source,
                                            text="Both source and actual target are approved.",
                                            authorization={**scope, "record_ids": [source, target]}))
        self.assertEqual(result["record_id"], target)
        self.assertIn("Both source and actual target are approved.", [event["text"] for event in history(self.store.read(), source)["events"]])

    def test_rollback_merge_requires_scope_for_both_affected_redirect_ids(self):
        source = self.create("Synthetic rollback source", request_id="rollback-source")["record_id"]
        target = self.create("Synthetic rollback target", request_id="rollback-target")["record_id"]
        self.store.apply(operation("merge", "rollback-merge", source_id=source, target_id=target,
                                   confirmed_same_object=True, confirmation_quote="These are the same synthetic object."))
        scope = {"basis": "approved_scope", "reference": "synthetic:rollback-merge-scope", "quote": "Only update target.",
                 "actions": ["rollback"], "record_ids": [target]}
        self.assert_rejected_without_mutation(operation("rollback", "rollback-merge-outside-source", target_request_id="rollback-merge",
                                              expected_revision=3, authorization=scope), "OUTSIDE_APPROVED_RECORDS")
        self.store.apply(operation("rollback", "rollback-merge-authorized", target_request_id="rollback-merge", expected_revision=3,
                                   authorization={**scope, "record_ids": [source, target]}))
        self.assertNotIn(source, self.store.read()["redirects"])

    def test_commit_failure_does_not_leave_a_partial_live_write(self):
        before = self.path.read_bytes()
        real_write = core.atomic_write
        def fail_live(path, raw):
            if Path(path) == self.path:
                raise OSError("Synthetic disk replacement failure")
            return real_write(path, raw)
        with mock.patch.object(core, "atomic_write", side_effect=fail_live):
            with self.assertRaisesRegex(OSError, "Synthetic disk"):
                self.create()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.read()["revision"], 0)
        self.assertEqual(self.store.read()["operations"], {})
        self.assertFalse(list(self.path.parent.glob(".sync-*.tmp")))

    def test_atomic_replace_failure_keeps_original_and_removes_temporary_file(self):
        before = self.path.read_bytes()
        with mock.patch.object(core.os, "replace", side_effect=OSError("Synthetic replace failure")):
            with self.assertRaisesRegex(OSError, "Synthetic replace"):
                atomic_write(self.path, b"partial replacement")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob(".sync-*.tmp")))

    @unittest.skipUnless(os.name == "nt", "Windows retry behavior has not been exercised on a Linux host")
    def test_windows_transient_permission_error_recovers_without_repeating_transaction(self):
        real_replace = core.os.replace
        for succeeds_on in (2, 3):
            with self.subTest(succeeds_on=succeeds_on):
                before = self.path.read_bytes()
                before_data = self.store.read()
                attempts = []
                def temporary_denial(src, dst):
                    if Path(dst) == self.path:
                        attempts.append((src, dst))
                        if len(attempts) < succeeds_on:
                            raise PermissionError(13, "Synthetic transient Windows file sharing denial")
                    return real_replace(src, dst)
                with mock.patch.object(core.os, "replace", side_effect=temporary_denial), mock.patch.object(core.time, "sleep") as sleep:
                    receipt = self.create("Synthetic Windows retry " + str(succeeds_on), request_id="windows-retry-" + str(succeeds_on))
                after = self.store.read()
                self.assertEqual(len(attempts), succeeds_on)
                self.assertEqual(len({src for src, dst in attempts}), 1)
                self.assertEqual(sleep.call_args_list, [mock.call(0.05 * (attempt + 1)) for attempt in range(succeeds_on - 1)])
                self.assertEqual(after["revision"], before_data["revision"] + 1)
                self.assertEqual(len(after["events"]), len(before_data["events"]) + 1)
                self.assertEqual(len(after["operations"]), len(before_data["operations"]) + 1)
                self.assertEqual((self.store.backups / (receipt["backup_sha256"] + ".json")).read_bytes(), before)
                self.assertFalse(list(self.path.parent.glob(".sync-*.tmp")))

    @unittest.skipUnless(os.name == "nt", "Windows retry behavior has not been exercised on a Linux host")
    def test_windows_persistent_permission_error_stops_after_four_attempts_and_preserves_archive(self):
        before = self.path.read_bytes()
        before_data = self.store.read()
        real_replace = core.os.replace
        attempts = []
        def persistent_denial(src, dst):
            if Path(dst) == self.path:
                attempts.append((src, dst))
                raise PermissionError(13, "Synthetic persistent Windows file sharing denial")
            return real_replace(src, dst)
        with mock.patch.object(core.os, "replace", side_effect=persistent_denial), mock.patch.object(core.time, "sleep") as sleep:
            with self.assertRaisesRegex(PermissionError, "Synthetic persistent Windows"):
                self.create("Synthetic blocked Windows write", request_id="windows-persistent-denial")
        self.assertEqual(len(attempts), 4)
        self.assertEqual(len({src for src, dst in attempts}), 1)
        self.assertEqual(sleep.call_args_list, [mock.call(0.05), mock.call(0.1), mock.call(0.05 * 3)])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.read(), before_data)
        self.assertNotIn("windows-persistent-denial", self.store.read()["operations"])
        self.assertFalse(list(self.path.parent.glob(".sync-*.tmp")))
        self.assertFalse(list(self.store.backups.glob(".sync-*.tmp")))
        backup = self.store.backups / (hashlib.sha256(before).hexdigest() + ".json")
        self.assertEqual(backup.read_bytes(), before)

    def test_corrupt_backup_prevents_live_mutation(self):
        before = self.path.read_bytes()
        key = hashlib.sha256(before).hexdigest()
        self.store.backups.mkdir()
        (self.store.backups / (key + ".json")).write_bytes(b"Synthetic corrupted backup")
        self.assert_rejected_without_mutation(operation("create", "backup-failure", record={"name": "Synthetic backup test"}),
                                              "BACKUP_CORRUPT")

    def test_unmigrated_store_refuses_auto_write(self):
        self.path.write_text('{"user_lib": [], "incub": []}', encoding="utf-8")
        self.assert_rejected_without_mutation(operation("create", "legacy-write", record={"name": "Synthetic legacy test"}),
                                              "MIGRATION_REQUIRED")

    def test_cli_error_is_machine_readable_and_does_not_write(self):
        before = self.path.read_bytes()
        payload = self.write_json("bad-request.json", operation("append", "unknown-record", record_id=str(uuid.uuid4()), text="Unknown record"))
        result = self.cli("apply", "--file", str(payload), check=False)
        self.assertFalse(result["ok"])
        self.assertIn("NOT_FOUND", result["error"])
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

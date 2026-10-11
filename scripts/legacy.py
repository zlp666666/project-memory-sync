"""Conservative, offline migration of an existing S7 archive.

No client database is opened. Legacy classifications and project assignments are
claims, not adopted state. Each distinct extracted occurrence stays independent
until a person confirms that it describes the same object as another record.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import pathlib
import uuid
from collections import Counter
from typing import Any


NAMESPACE = uuid.UUID("5370508c-13d3-4f60-9633-f9a18fa7f644")
DERIVED_LEGACY_FIELDS = {"incub_rank", "incub_score"}


class MigrationError(ValueError):
    """Input cannot be safely interpreted; the caller must not write a store."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _id(kind: str, value: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, kind + ":" + _fingerprint(value)))


def _base_record(record_id: str, kind: str, name: str, origin: str = "uncertain") -> dict:
    return {
        "id": record_id,
        "kind": kind,
        "name": name,
        "aliases": [],
        "origin": origin,
        "depth": "unknown",
        "progress": "unknown",
        "intent": "undecided",
        "evidence_status": "unverified",
        "created_at": "",
        "updated_at": "",
    }


def _event(record_id: str, fingerprint: str, text: str, source: dict,
           occurred_at: str = "") -> dict:
    return {
        "id": _id("legacy-event", {"record_id": record_id, "fingerprint": fingerprint}),
        "record_id": record_id,
        "kind": "legacy_import",
        "text": text,
        "occurred_at": occurred_at,
        "source": {"quote": "", "evidence_status": "unverified",
                   "quote_kind": "extracted_summary", **source},
        "changes": {},
    }


def _load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationError("Cannot read migration input: " + path.name) from error


def _source_hashes(archive: pathlib.Path) -> dict[str, str]:
    """Relative source fingerprints only; never copy a client path inventory."""
    paths = [archive / "logs" / "s7-library.json", archive / "inventory.csv", archive / "project-index.json"]
    paths.extend(sorted((archive / "batches").glob("*.jsonl")))
    result = {}
    for path in paths:
        if path.exists():
            try:
                result[path.relative_to(archive).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise MigrationError("Cannot fingerprint migration input: " + path.name) from error
    return result


def _legacy_payload(item: dict) -> dict:
    return {key: value for key, value in item.items() if key not in DERIVED_LEGACY_FIELDS}


def _named_items(legacy: dict, section: str) -> list[dict]:
    items = legacy.get(section, [])
    if not isinstance(items, list):
        raise MigrationError("Legacy section is not a list: " + section)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise MigrationError("Legacy section has an unnamed or invalid record: " + section)
    return items


def _text(extraction: dict) -> str:
    # Retain the full extraction separately too; this is a readable history view.
    pieces = [str(extraction.get("name", ""))]
    for key in ("problem", "work_done", "stop_reason", "note"):
        value = extraction.get(key)
        if value:
            pieces.append(key + ": " + str(value))
    return "\n".join(pieces)


def _batch_context(row: dict, ref: str, occurred_at: str) -> dict:
    """Keep alternative extractor context without adopting any of its links."""
    return {
        "ref": ref,
        "occurred_at": occurred_at,
        "title": row.get("title", ""),
        "goal": row.get("goal", ""),
        "projects": copy.deepcopy(row.get("projects") or []),
        "tier": row.get("tier", ""),
        "status": row.get("status", ""),
        "quality_flags": row.get("quality_flags", ""),
    }


def build_migration(archive: pathlib.Path, legacy: dict) -> dict:
    """Build a complete schema-3 snapshot without modifying any file.

    Stable UUID5s use complete immutable source fingerprints, never list position,
    sort order, or a name prefix. Store identity is newly assigned once by the
    caller's atomic migration. Re-running this builder is for verification, not
    for replacing an already migrated live store.
    """
    archive = pathlib.Path(archive)
    if not isinstance(legacy, dict) or legacy.get("schema_version", 0) >= 3:
        raise MigrationError("Expected an unmigrated S7 dictionary")
    source_hashes = _source_hashes(archive)

    records: dict[str, dict] = {}
    events: dict[str, dict] = {}
    selection_map = {"user_lib": {}, "user_lib_visible": {}, "ai_zone": {}, "qa_zone": {}, "incub": {}}
    sections = {name: _named_items(legacy, name) for name in ("user_lib", "ai_zone", "qa_zone", "incub")}
    legacy_ids: dict[str, str] = {}
    for section in ("user_lib", "ai_zone", "qa_zone"):
        prefix = {"user_lib": "u", "ai_zone": "a", "qa_zone": "q"}[section]
        for number, item in enumerate(sections[section], 1):
            payload = _legacy_payload(item)
            record_id = _id("legacy-s7-record", payload)
            legacy_ids[_canonical(payload)] = record_id
            selection_map[section][prefix + str(number)] = record_id
            if record_id in records:
                records[record_id]["legacy_sections"].append(section)
                continue
            record = _base_record(record_id, "idea", item["name"])
            record["legacy_sections"] = [section]
            record["legacy_claims"] = copy.deepcopy(item)
            record["migration_source"] = "legacy_s7_summary"
            records[record_id] = record
            event = _event(record_id, _fingerprint(payload), item["name"], {
                "ref": "logs/s7-library.json#legacy_data/" + section + ":" + _fingerprint(payload),
                "legacy_claims": copy.deepcopy(payload),
                "warning": "Legacy ownership, depth, progress and project links require review.",
            })
            events[event["id"]] = event

    # Both legacy views are recorded because the old HTML assigned uN after
    # sorting, while its export assigned uN in source order. Never import old
    # localStorage decisions automatically through either map.
    depth_order = {"原型尝试": 0, "深入探索": 1, "初步了解": 2, "灵感": 3}
    visible = []
    for status in ("暂时搁置", "已完成", "已并入", "已放弃"):
        visible.extend(sorted((item for item in sections["user_lib"] if item.get("status") == status),
                              key=lambda item: (depth_order.get(item.get("depth"), 9), item["name"])))
    for number, item in enumerate(visible, 1):
        selection_map["user_lib_visible"]["u" + str(number)] = legacy_ids[_canonical(_legacy_payload(item))]

    for number, item in enumerate(sections["incub"], 1):
        record_id = legacy_ids.get(_canonical(_legacy_payload(item)))
        if record_id is None:
            raise MigrationError("Incubation record does not match a complete legacy library record")
        rank = item.get("incub_rank", number)
        key = "n" + str(rank)
        if key in selection_map["incub"]:
            raise MigrationError("Duplicate incubation selection key")
        selection_map["incub"][key] = record_id

    inventory: dict[str, dict] = {}
    inventory_path = archive / "inventory.csv"
    if inventory_path.exists():
        try:
            with inventory_path.open(encoding="utf-8-sig", newline="") as handle:
                inventory = {row["uid"]: {"start": row.get("start", ""), "source": row.get("source", "")}
                             for row in csv.DictReader(handle) if row.get("uid")}
        except (OSError, UnicodeError, csv.Error) as error:
            raise MigrationError("Cannot read migration inventory") from error

    batch_statuses: Counter = Counter()
    occurrence_ids: set[str] = set()
    occurrence_events: dict[str, dict] = {}
    batch_files = sorted((archive / "batches").glob("*.jsonl"))
    batch_rows = 0
    missing_uid_rows = 0
    unnamed_ideas = 0
    source_rows_without_ideas = 0
    for path in batch_files:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as error:
            raise MigrationError("Cannot read migration batch: " + path.name) from error
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise MigrationError("Invalid batch JSON at " + path.name + ":" + str(line_number)) from error
            if not isinstance(row, dict):
                raise MigrationError("Non-object batch row at " + path.name + ":" + str(line_number))
            batch_rows += 1
            batch_statuses[str(row.get("status", "unknown"))] += 1
            uid = str(row.get("uid") or "")
            if not uid:
                missing_uid_rows += 1
            inv = inventory.get(uid, {})
            client = str(row.get("source") or inv.get("source") or "unknown")
            occurred_at = str(row.get("start") or inv.get("start") or "")
            ideas = row.get("ideas") or row.get("idea") or []
            if not isinstance(ideas, list):
                raise MigrationError("Non-list ideas at " + path.name + ":" + str(line_number))
            if not ideas:
                source_rows_without_ideas += 1
            for extraction in ideas:
                if not isinstance(extraction, dict):
                    raise MigrationError("Non-object extracted idea at " + path.name + ":" + str(line_number))
                name = extraction.get("name") or extraction.get("text") or ""
                if not isinstance(name, str) or not name.strip():
                    unnamed_ideas += 1
                    continue
                identity = {"client": client, "uid": uid, "extraction": extraction}
                if not uid:
                    identity["missing_uid_row_fingerprint"] = _fingerprint(row)
                record_id = _id("batch-idea-occurrence", identity)
                ref = "batches/" + path.name + ":" + str(line_number)
                if record_id in occurrence_ids:
                    existing = occurrence_events[record_id]["source"]
                    if ref not in existing["refs"]:
                        existing["refs"].append(ref)
                        existing["context_variants"].append(_batch_context(row, ref, occurred_at))
                    if occurred_at and not occurrence_events[record_id]["occurred_at"]:
                        occurrence_events[record_id]["occurred_at"] = occurred_at
                        records[record_id]["created_at"] = records[record_id]["updated_at"] = occurred_at
                    continue
                origin = {"user": "user_proposed", "ai": "ai_suggested", "user_approved": "user_approved"}.get(
                    extraction.get("originator"), "uncertain")
                record = _base_record(record_id, "idea", name, origin)
                record["created_at"] = record["updated_at"] = occurred_at
                record["migration_source"] = "batch_extracted_occurrence"
                # Context is a candidate list, never an adopted project relation.
                record["unverified_project_mentions"] = [project.get("name", "") for project in (row.get("projects") or [])
                                                          if isinstance(project, dict) and project.get("name")]
                record["legacy_claims"] = {"originator": extraction.get("originator")}
                records[record_id] = record
                source = {"ref": ref, "refs": [ref], "uid": uid, "client": client,
                          "extraction_fingerprint": _fingerprint(extraction),
                          "extracted_idea": copy.deepcopy(extraction),
                          "context_projects": copy.deepcopy(row.get("projects", [])),
                          "context_variants": [_batch_context(row, ref, occurred_at)],
                          "tier": row.get("tier", ""), "quality_flags": row.get("quality_flags", ""),
                          "warning": "Extracted history is not a verbatim user quote; context projects are candidates only."}
                event = _event(record_id, _fingerprint(identity), _text(extraction), source, occurred_at)
                events[event["id"]] = event
                occurrence_events[record_id] = event
                occurrence_ids.add(record_id)

    project_count = 0
    index_path = archive / "project-index.json"
    if index_path.exists():
        index = _load_json(index_path)
        if not isinstance(index, dict) or not isinstance(index.get("projects", {}), dict):
            raise MigrationError("Invalid historical project index")
        for key, summary in index.get("projects", {}).items():
            if not isinstance(summary, dict) or not isinstance(summary.get("name"), str) or not summary["name"].strip():
                raise MigrationError("Unnamed or invalid historical project summary")
            identity = {"key": key, "summary": summary}
            record_id = _id("project-index-summary", identity)
            record = _base_record(record_id, "project", summary["name"])
            record["aliases"] = sorted(set(alias for alias in summary.get("aliases", []) if isinstance(alias, str)))
            record["migration_source"] = "historical_project_summary"
            record["legacy_claims"] = copy.deepcopy(summary)
            record["formal_project_card"] = False
            records[record_id] = record
            event = _event(record_id, _fingerprint(identity), summary["name"], {
                "ref": "project-index.json#" + _fingerprint(identity),
                "extracted_project_summary": copy.deepcopy(summary),
                "warning": "Historical cluster summary; no formal project card was created.",
            })
            events[event["id"]] = event
            project_count += 1

    misaligned_ids = sum(selection_map["user_lib"].get(key) != record_id
                         for key, record_id in selection_map["user_lib_visible"].items())
    audit = {
        "legacy_user_records": len(sections["user_lib"]),
        "legacy_ai_records": len(sections["ai_zone"]),
        "legacy_qa_records": len(sections["qa_zone"]),
        "incubation_links": len(selection_map["incub"]),
        "legacy_selection_mismatches": misaligned_ids,
        "batch_files": len(batch_files), "batch_rows": batch_rows,
        "batch_statuses": dict(sorted(batch_statuses.items())),
        "batch_rows_missing_uid": missing_uid_rows,
        "batch_rows_without_ideas": source_rows_without_ideas,
        "unnamed_extractions_skipped": unnamed_ideas,
        "independent_idea_occurrences": len(occurrence_ids),
        "historical_project_summaries": project_count,
        "records": len(records), "events": len(events),
        "automatic_project_assignments": 0,
        "automatic_depth_adoptions": 0,
        "automatic_name_merges": 0,
    }
    if source_hashes != _source_hashes(archive):
        raise MigrationError("Migration inputs changed while building the snapshot; retry with stable inputs")
    return {
        "schema_version": 3, "store_id": str(uuid.uuid4()), "revision": 1,
        "records": records,
        "events": sorted(events.values(), key=lambda item: (item["occurred_at"], item["id"])),
        "operations": {}, "redirects": {},
        "migration": {
            "strategy": "complete_source_fingerprints_and_independent_occurrences",
            "version": 1, "selection_map": selection_map, "audit": audit,
            "source_files_sha256": source_hashes,
            "legacy_payload_sha256": _fingerprint(legacy),
            "coverage": "Historical extracted summaries from the supplied archive only. "
                        "Failed, light-scan and metadata-only rows do not establish full conversation coverage. "
                        "Missing records are not evidence of no prior work. User wording, classifications, "
                        "depth and project links remain unverified unless reviewed against their sources.",
            "unsafe_legacy_decisions": {"storage_key": "s7-decisions-v1", "require_review": True,
                                        "reason": "Legacy visible and exported u IDs disagree; n selections were not exported."},
            "limitations": ["Historical extracts do not prove exact user wording.",
                            "Different names and occurrences require an explicitly confirmed merge.",
                            "History outside the supplied archive was not inspected.",
                            "Legacy project links and depth labels remain unverified claims."],
        },
        "legacy_data": copy.deepcopy(legacy),
    }

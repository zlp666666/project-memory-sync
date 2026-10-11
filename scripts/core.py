"""Single-document transactions for a local, existing project archive. Standard library only."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.1"
ORIGINS = {"user_proposed", "user_approved", "ai_suggested", "knowledge_qa", "uncertain"}
DEPTHS = {"idea", "inquiry", "research", "prototype", "unknown"}
PROGRESS = {"unknown", "discussed", "researched", "developed", "failed", "paused", "completed", "abandoned"}
INTENTS = {"undecided", "archive", "keep", "restart", "join_existing"}
EVIDENCE = {"verified", "unverified", "inference"}


class SyncError(Exception):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def ident():
    return str(uuid.uuid4())


def norm(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def features(value):
    text = norm(value)
    words = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", value.casefold())
    return set(words) | {text[i:i + 2] for i in range(max(0, len(text) - 1))}


def empty_store():
    return {"schema_version": 3, "store_id": ident(), "revision": 0,
            "records": {}, "events": [], "operations": {}, "redirects": {}}


def validate(data):
    if not isinstance(data, dict) or data.get("schema_version") != 3:
        raise SyncError("MIGRATION_REQUIRED: legacy data must be audited and migrated before writing")
    try:
        uuid.UUID(data["store_id"])
        assert type(data["revision"]) is int and data["revision"] >= 0
        assert isinstance(data["records"], dict) and isinstance(data["operations"], dict)
        assert isinstance(data["redirects"], dict) and isinstance(data["events"], list)
        for key, rec in data["records"].items():
            uuid.UUID(key)
            assert rec["id"] == key and rec["kind"] in {"idea", "project"}
            assert isinstance(rec["name"], str) and rec["name"].strip()
            assert isinstance(rec["aliases"], list) and all(isinstance(x, str) for x in rec["aliases"])
            assert rec["origin"] in ORIGINS and rec["depth"] in DEPTHS
            assert rec["progress"] in PROGRESS and rec["intent"] in INTENTS
            assert rec["evidence_status"] in EVIDENCE
        event_ids = set()
        for ev in data["events"]:
            uuid.UUID(ev["id"])
            assert ev["id"] not in event_ids
            event_ids.add(ev["id"])
            assert ev["record_id"] in data["records"] or ev["record_id"] is None
            assert ev["source"]["evidence_status"] in EVIDENCE
            assert isinstance(ev["source"].get("ref"), str) and ev["source"]["ref"].strip()
            assert isinstance(ev["source"].get("quote"), str)
            assert isinstance(ev.get("text"), str) and isinstance(ev.get("occurred_at"), str)
        for old, target in data["redirects"].items():
            assert old in data["records"] and target in data["records"] and old != target
            resolved(data, old)
        for request_id, op in data["operations"].items():
            assert isinstance(request_id, str) and request_id
            assert isinstance(op["hash"], str) and len(op["hash"]) == 64
            assert 0 < op["revision"] <= data["revision"]
    except (KeyError, ValueError, TypeError, AssertionError) as exc:
        raise SyncError("INVALID_STORE: schema or integrity check failed") from exc
    return data


def resolved(data, record_id):
    seen = set()
    while record_id in data["redirects"]:
        if record_id in seen:
            raise SyncError("REDIRECT_CYCLE")
        seen.add(record_id)
        record_id = data["redirects"][record_id]
    if record_id not in data["records"]:
        raise SyncError("NOT_FOUND: unknown record ID")
    return record_id


def search(data, query, limit=20, semantic_candidates=None):
    """Lexical candidates plus optional agent-supplied semantic candidates; never auto merge."""
    validate(data)
    if not isinstance(query, str) or not norm(query):
        raise SyncError("EMPTY_QUERY")
    if semantic_candidates is not None and (not isinstance(semantic_candidates, list) or any(not isinstance(x, dict) for x in semantic_candidates)):
        raise SyncError("INVALID_SEMANTIC_CANDIDATES")
    q = norm(query)
    qf = features(query)
    docs = {k: features(r["name"] + " " + " ".join(r["aliases"])) for k, r in data["records"].items()}
    counts = {}
    for f in docs.values():
        for token in f:
            counts[token] = counts.get(token, 0) + 1
    weights = {t: math.log((len(docs) + 1) / (v + 1)) + 1 for t, v in counts.items()}
    results = {}
    for key, rec in data["records"].items():
        if key in data["redirects"] or rec.get("rolled_back"):
            continue
        labels = [rec["name"], *rec["aliases"]]
        exact = any(norm(t) == q for t in labels)
        substring = any(q in norm(t) or norm(t) in q for t in labels if norm(t))
        inter = qf & docs[key]
        numerator = sum(weights.get(t, 1) for t in inter)
        denominator = math.sqrt(sum(weights.get(t, 1) for t in qf) * sum(weights.get(t, 1) for t in docs[key]))
        score = 1.0 if exact else max(0.7 if substring else 0, numerator / denominator if denominator else 0)
        if score >= 0.12:
            results[key] = {"id": key, "name": rec["name"], "kind": rec["kind"], "origin": rec["origin"],
                            "score": round(score, 4), "match": "exact" if exact else "lexical_candidate",
                            "evidence_status": rec["evidence_status"], "automatic_merge": False}
    for item in semantic_candidates or []:
        key = resolved(data, item["id"])
        if data["records"][key].get("rolled_back"):
            continue
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise SyncError("SEMANTIC_REASON_REQUIRED")
        rec = data["records"][key]
        results.setdefault(key, {"id": key, "name": rec["name"], "kind": rec["kind"], "origin": rec["origin"],
                                "score": 0, "match": "agent_semantic_candidate",
                                "evidence_status": rec["evidence_status"], "automatic_merge": False})
        results[key]["semantic_reason"] = item["reason"]
    return sorted(results.values(), key=lambda r: (-r["score"], r["id"]))[:limit]


def history(data, record_id):
    key = resolved(data, record_id)
    family = {k for k in data["records"] if resolved(data, k) == key}
    events = [e for e in data["events"] if e["record_id"] in family]
    def timeline_key(ev):
        try:
            value = datetime.fromisoformat(ev.get("occurred_at", "").replace("Z", "+00:00"))
            value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
            timestamp = value.timestamp()
        except (ValueError, TypeError, OverflowError):
            timestamp = float("-inf")
        return (timestamp, ev.get("recorded_at", ""), ev["id"])
    return {"record": data["records"][key], "requested_id": record_id, "associated_ids": sorted(family),
            "events": sorted(events, key=timeline_key),
            "coverage": data.get("migration", {}).get("coverage", "Only recorded evidence; absence is not proof of no prior work")}


@contextmanager
def locked(path, timeout=15):
    """Kernel-released advisory lock. All writers must use this API; no stale lock removal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SyncError("LOCK_TIMEOUT: retry once after checking the active writer")
                time.sleep(0.025)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write(path, raw, overwrite=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sync-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            # Windows indexers may hold a freshly read file briefly. The caller's
            # lock stays held; bounded retry never discards the pre-image.
            for attempt in range(4):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if os.name != "nt" or attempt == 3:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        else:
            # Atomic publication that fails if another writer already created the target.
            os.link(temporary, path)
            os.unlink(temporary)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def authorize(payload, ids=()):
    auth = payload.get("authorization", {})
    if not isinstance(auth, dict) or not isinstance(auth.get("basis"), str) or auth["basis"] not in {"explicit_user", "approved_scope"}:
        raise SyncError("AUTHORIZATION_REQUIRED: user instruction or documented approved scope")
    if any(not isinstance(auth.get(k), str) or not auth[k].strip() for k in ("reference", "quote")):
        raise SyncError("AUTHORIZATION_REQUIRED: user instruction or documented approved scope")
    if auth["basis"] == "approved_scope":
        if any(not isinstance(auth.get(k), list) or not all(isinstance(x, str) for x in auth[k]) for k in ("actions", "record_ids")):
            raise SyncError("INVALID_APPROVED_SCOPE")
        if payload["action"] not in auth.get("actions", []):
            raise SyncError("OUTSIDE_APPROVED_ACTIONS")
        allowed = auth.get("record_ids", [])
        if "*" not in allowed and any(i not in allowed for i in ids):
            raise SyncError("OUTSIDE_APPROVED_RECORDS")


def checked_source(source):
    if not isinstance(source, dict) or not isinstance(source.get("ref"), str) or not source["ref"].strip() or not isinstance(source.get("quote"), str):
        raise SyncError("SOURCE_REQUIRED: preserve a source reference and the actual quote or explicitly empty historical quote")
    if not isinstance(source.get("evidence_status"), str) or source["evidence_status"] not in EVIDENCE:
        raise SyncError("EVIDENCE_STATUS_REQUIRED")
    return copy.deepcopy(source)


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.lock = self.path.with_name(self.path.name + ".lock")
        self.backups = self.path.parent / ".memory-sync-backups"

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise SyncError("READ_FAILED: existing archive missing or invalid JSON") from exc
        return validate(data)

    def backup(self, raw):
        key = hashlib.sha256(raw).hexdigest()
        path = self.backups / (key + ".json")
        if not path.exists():
            atomic_write(path, raw)
        if hashlib.sha256(path.read_bytes()).hexdigest() != key:
            raise SyncError("BACKUP_CORRUPT")
        return key

    def commit(self, old_raw, data):
        validate(data)
        key = self.backup(old_raw)
        atomic_write(self.path, (canonical(data) + "\n").encode("utf-8"))
        return key

    def apply(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("request_id"), str) or not payload["request_id"].strip():
            raise SyncError("REQUEST_ID_REQUIRED: reuse it unchanged when retrying")
        if payload.get("action") not in {"create", "append", "merge", "alias", "decision", "rollback"}:
            raise SyncError("UNKNOWN_ACTION")
        authorize(payload, [payload[k] for k in ("record_id", "source_id", "target_id") if k in payload])
        fingerprint = digest(payload)
        with locked(self.lock):
            old_raw = self.path.read_bytes()
            data = self.read()
            old = data["operations"].get(payload["request_id"])
            if old:
                if old["hash"] != fingerprint:
                    raise SyncError("IDEMPOTENCY_CONFLICT: same request ID with different content")
                return {**old["receipt"], "saved_path": str(self.path), "duplicate": True, "operation_status": old.get("status", "active")}
            if "expected_revision" in payload and payload["expected_revision"] != data["revision"]:
                raise SyncError("REVISION_CONFLICT")
            effective_ids = [resolved(data, payload[k]) for k in ("record_id", "source_id", "target_id") if k in payload]
            if payload["action"] == "rollback":
                rollback_target = data["operations"].get(payload.get("target_request_id"))
                if rollback_target:
                    effective_ids.extend(rollback_target["undo"]["records"])
                    effective_ids.append(rollback_target["receipt"]["record_id"])
                    redirected = rollback_target["receipt"]["actual_update"].get("changes", {}).get("redirect", {})
                    effective_ids.extend(redirected)
                    effective_ids.extend(redirected.values())
            authorize(payload, effective_ids)
            if payload["action"] == "create" and payload["authorization"]["basis"] == "approved_scope":
                if "*" not in payload["authorization"].get("record_ids", []):
                    raise SyncError("OUTSIDE_APPROVED_RECORDS: new idea requires explicitly scoped creation")
            before_records = copy.deepcopy(data["records"])
            before_redirects = copy.deepcopy(data["redirects"])
            event = {"id": ident(), "record_id": None, "kind": payload["action"], "text": payload.get("text", ""),
                     "occurred_at": payload.get("occurred_at", now()), "recorded_at": now(),
                     "source": checked_source(payload.get("source")), "authorization": payload["authorization"],
                     "actor": payload.get("actor", "unspecified"), "request_id": payload["request_id"], "changes": {}}
            if not isinstance(event["text"], str) or not isinstance(event["occurred_at"], str):
                raise SyncError("EVENT_TEXT_AND_TIME_MUST_BE_STRINGS")
            action = payload["action"]
            if action == "create":
                rec = copy.deepcopy(payload.get("record", {}))
                if not isinstance(rec, dict):
                    raise SyncError("RECORD_MUST_BE_OBJECT")
                if rec.get("kind", "idea") != "idea":
                    raise SyncError("NO_FORMAL_PROJECT_CREATION: append a feature to an existing project ID")
                name = rec.get("name", "")
                candidates = search(data, name)
                if any(c["match"] == "exact" for c in candidates):
                    if not payload.get("confirmed_distinct") or not payload.get("confirmation_quote"):
                        raise SyncError("DUPLICATE_CANDIDATE: resolve exact name/alias or confirm distinct object")
                key = ident()
                rec = {"id": key, "kind": "idea", "name": name, "aliases": rec.get("aliases", []),
                       "origin": rec.get("origin", "user_proposed"), "depth": rec.get("depth", "idea"),
                       "progress": rec.get("progress", "unknown"), "intent": rec.get("intent", "undecided"),
                       "evidence_status": rec.get("evidence_status", event["source"]["evidence_status"])}
                if rec["origin"] in {"ai_suggested", "knowledge_qa", "uncertain"} and rec["intent"] not in {"undecided", "archive"}:
                    raise SyncError("UNCONFIRMED_INTENT")
                if rec["origin"] == "user_approved" and not payload.get("approval_quote"):
                    raise SyncError("USER_APPROVAL_QUOTE_REQUIRED")
                data["records"][key] = rec
                event["changes"] = {"created_record": copy.deepcopy(rec)}
                event["lookup_candidates"] = candidates[:10]
            elif action in {"append", "alias", "decision"}:
                key = resolved(data, payload["record_id"])
                rec = data["records"][key]
                if action == "alias":
                    alias = payload.get("alias", "")
                    if not isinstance(alias, str) or not norm(alias):
                        raise SyncError("ALIAS_REQUIRED")
                    if alias not in rec["aliases"]:
                        rec["aliases"].append(alias)
                    event["changes"] = {"aliases": copy.deepcopy(rec["aliases"])}
                else:
                    changes = payload.get("changes", {})
                    if not isinstance(changes, dict) or not isinstance(payload.get("details", {}), dict):
                        raise SyncError("CHANGES_AND_DETAILS_MUST_BE_OBJECTS")
                    if set(changes) - {"origin", "depth", "progress", "intent", "evidence_status"}:
                        raise SyncError("UNSUPPORTED_CHANGE")
                    if "intent" in changes and action != "decision":
                        raise SyncError("INTENT_REQUIRES_DECISION_ACTION")
                    if changes.get("origin") in {"user_proposed", "user_approved"} and rec["origin"] not in {"user_proposed", "user_approved"}:
                        if not payload.get("approval_quote"):
                            raise SyncError("USER_APPROVAL_QUOTE_REQUIRED")
                    if changes.get("intent", rec["intent"]) not in {"undecided", "archive"} and changes.get("origin", rec["origin"]) in {"ai_suggested", "knowledge_qa", "uncertain"}:
                        raise SyncError("UNCONFIRMED_INTENT")
                    if not event["text"].strip():
                        raise SyncError("EVENT_TEXT_REQUIRED")
                    rec.update(changes)
                    event["changes"] = copy.deepcopy(changes)
                    event["event_type"] = payload.get("event_type", "discussion")
                    event["details"] = copy.deepcopy(payload.get("details", {}))
            elif action == "merge":
                if not payload.get("confirmed_same_object") or not payload.get("confirmation_quote"):
                    raise SyncError("MANUAL_MERGE_CONFIRMATION_REQUIRED")
                old_key = resolved(data, payload["source_id"])
                key = resolved(data, payload["target_id"])
                if old_key == key:
                    raise SyncError("ALREADY_SAME_OBJECT")
                if data["records"][old_key]["kind"] != data["records"][key]["kind"]:
                    raise SyncError("KIND_MISMATCH: relate a feature using append, do not merge idea and project")
                data["redirects"][old_key] = key
                rec = data["records"][key]
                for label in [data["records"][old_key]["name"], *data["records"][old_key]["aliases"]]:
                    if label != rec["name"] and label not in rec["aliases"]:
                        rec["aliases"].append(label)
                event["changes"] = {"redirect": {old_key: key}}
                event["confirmation_quote"] = payload["confirmation_quote"]
            else:
                if "expected_revision" not in payload:
                    raise SyncError("ROLLBACK_REVISION_REQUIRED")
                target = data["operations"].get(payload.get("target_request_id"))
                if not target or target["revision"] != data["revision"] or target.get("status") == "reverted":
                    raise SyncError("ROLLBACK_ONLY_LATEST: later writes must not be discarded")
                if target["receipt"]["action"] == "rollback":
                    raise SyncError("ROLLBACK_OF_ROLLBACK_NOT_SUPPORTED: append an explicit corrective event")
                for k, prev in target["undo"]["records"].items():
                    if prev is None:
                        data["records"][k]["rolled_back"] = True
                        data["records"][k]["intent"] = "archive"
                    else:
                        data["records"][k] = prev
                data["redirects"] = target["undo"]["redirects"]
                target["status"] = "reverted"
                key = target["receipt"]["record_id"]
                event["changes"] = {"reverted_request_id": payload["target_request_id"]}
            event["record_id"] = key
            data["events"].append(event)
            data["revision"] += 1
            changed = {k: before_records.get(k) for k, r in data["records"].items() if before_records.get(k) != r}
            receipt = {"ok": True, "request_id": payload["request_id"], "record_id": key,
                       "event_id": event["id"], "revision": data["revision"], "saved_path": str(self.path),
                       "action": action, "actual_update": event, "duplicate": False}
            data["operations"][payload["request_id"]] = {"hash": fingerprint, "revision": data["revision"],
                    "receipt": receipt, "status": "active", "undo": {"records": changed, "redirects": before_redirects}}
            key_backup = self.commit(old_raw, data)
            return {**receipt, "backup_sha256": key_backup}

    def import_document(self, incoming, expected_revision, authorization):
        """Additive import. Conflict never overwrites a divergent record or history."""
        validate(incoming)
        authorize({"action": "import", "authorization": authorization}, list(incoming["records"]))
        with locked(self.lock):
            raw = self.path.read_bytes()
            data = self.read()
            if data["revision"] != expected_revision:
                raise SyncError("REVISION_CONFLICT")
            if data["store_id"] != incoming["store_id"]:
                raise SyncError("STORE_ID_MISMATCH")
            for field in ("records", "operations", "redirects"):
                for key, value in incoming[field].items():
                    if key in data[field] and data[field][key] != value:
                        raise SyncError("IMPORT_CONFLICT: divergent " + field)
            existing = {e["id"]: e for e in data["events"]}
            for ev in incoming["events"]:
                if ev["id"] in existing and existing[ev["id"]] != ev:
                    raise SyncError("IMPORT_CONFLICT: divergent event")
            new_count = sum(key not in data["records"] for key in incoming["records"])
            new_events = [ev for ev in incoming["events"] if ev["id"] not in existing]
            new_metadata = any(key not in data[field] for field in ("operations", "redirects") for key in incoming[field])
            if not new_count and not new_events and not new_metadata:
                return {"ok": True, "duplicate": True, "revision": data["revision"], "saved_path": str(self.path)}
            for field in ("records", "operations", "redirects"):
                data[field].update(copy.deepcopy(incoming[field]))
            data["events"].extend(copy.deepcopy(new_events))
            data["revision"] = max(data["revision"], incoming["revision"]) + 1
            data["events"].append({"id": ident(), "record_id": None, "kind": "import", "text": "Additive import",
                                  "occurred_at": now(), "recorded_at": now(), "authorization": authorization,
                                  "source": {"ref": "export:" + digest(incoming), "quote": "", "evidence_status": "verified"},
                                  "changes": {"new_records": new_count, "new_events": len(new_events)}})
            backup = self.commit(raw, data)
            return {"ok": True, "revision": data["revision"], "saved_path": str(self.path),
                    "new_records": new_count, "new_events": len(new_events), "backup_sha256": backup}

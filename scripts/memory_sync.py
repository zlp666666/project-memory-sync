#!/usr/bin/env python3
"""JSON CLI for an existing local archive. No network, no database, no client install."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

if __package__:
    from .core import Store, SyncError, atomic_write, authorize, canonical, digest, history, locked, search, validate, VERSION
    from .legacy import build_migration
else:
    from core import Store, SyncError, atomic_write, authorize, canonical, digest, history, locked, search, validate, VERSION
    from legacy import build_migration


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def store_for(args):
    # Explicit paths retain the original CLI behavior. A saved connection is used
    # only when the caller did not choose another archive/environment/config.
    config_path = args.config
    default_config = Path(__file__).resolve().parents[1] / "local-config.json"
    if not config_path and not args.archive and not os.environ.get("PROJECT_MEMORY_ARCHIVE") and default_config.is_file():
        config_path = str(default_config)
    config = load(config_path) if config_path else {}
    if not isinstance(config, dict):
        raise SyncError("CONFIG_OBJECT_REQUIRED")
    archive = args.archive or os.environ.get("PROJECT_MEMORY_ARCHIVE") or config.get("archive")
    if not archive:
        raise SyncError("ARCHIVE_REQUIRED: --archive, --config or PROJECT_MEMORY_ARCHIVE")
    root = Path(archive).expanduser().resolve()
    if not root.is_dir():
        raise SyncError("EXISTING_ARCHIVE_REQUIRED")
    repo = Path(__file__).resolve().parents[1]
    if root == repo or repo in root.parents:
        raise SyncError("PRIVATE_DATA_IN_CODE_REPOSITORY_FORBIDDEN")
    if "connection_schema" in config:
        if __package__:
            from .bootstrap import validate_connection
        else:
            from bootstrap import validate_connection
        validate_connection(config, archive)
        # Even migrate's preview creates a cooperative lock in the legacy CLI.
        writes_archive = args.command in ("apply", "import", "migrate")
        if writes_archive and config["access"] != "read_write":
            raise SyncError("CONNECTION_READ_ONLY")
    return root, Store(root / "logs" / "s7-library.json")


def render(data, output):
    """Stable IDs are shared across all views. Browser storage is scoped to store UUID."""
    records = [r for k, r in data["records"].items() if k not in data["redirects"] and not r.get("rolled_back")]
    incub = list(dict.fromkeys(data.get("migration", {}).get("selection_map", {}).get("incub", {}).values()))
    # Avoid an embedded </script> in evidence becoming executable HTML.
    embedded = canonical({"store_id": data["store_id"], "revision": data["revision"], "records": records,
                          "incubation_ids": incub}).replace("<", "\\u003c").replace("\u2028", "\\u2028")
    html = '''<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>灵感与探索档案 · 稳定 ID</title>
<style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 20px;background:#f8fafc;color:#172a3a}input,select,button{font:inherit;padding:8px}article{background:white;padding:18px;margin:12px 0;border:1px solid #dfe7ed;border-radius:12px}small{display:block;color:#50667a;overflow-wrap:anywhere}.controls{display:flex;gap:12px;flex-wrap:wrap}h2{font-size:18px}p{line-height:1.6}</style>
<h1>灵感与探索档案</h1><p>历史分类和深度是待核对主张。这里的选择只生成待执行请求，不会直接修改灵感库。</p>
<div class="controls"><input id="q" placeholder="搜索名称或别名"><select id="view"><option value="all">完整库</option><option value="incub">旧版孵化精选</option><option value="project">历史项目</option></select><button id="export">导出选择</button></div><p id="count"></p><main id="rows"></main>
<script>const D=DATA, storageKey='memory-sync:'+D.store_id, choices=JSON.parse(localStorage.getItem(storageKey)||'{}');
const q=document.getElementById('q'),view=document.getElementById('view'),rows=document.getElementById('rows');
const options=[['','暂不决定'],['keep','保留'],['restart','重启'],['join_existing','归入已有项目'],['archive','仅存档']];
function draw(){rows.replaceChildren();const filtered=D.records.filter(r=>(view.value==='all'||view.value==='incub'&&D.incubation_ids.includes(r.id)||view.value==='project'&&r.kind==='project')&&[r.name,...r.aliases].some(t=>t.toLowerCase().includes(q.value.toLowerCase())));document.getElementById('count').textContent=filtered.length+' 条 / 当前已选 '+Object.keys(choices).length+' 条';for(const r of filtered){const card=document.createElement('article'),title=document.createElement('h2'),info=document.createElement('small'),select=document.createElement('select'),note=document.createElement('input');title.textContent=r.name;info.textContent=r.id+' · '+r.origin+' · '+r.depth+' · '+r.progress+' · '+r.intent+' · '+r.evidence_status;for(const [v,t] of options){const o=document.createElement('option');o.value=v;o.textContent=t;select.append(o)}select.value=choices[r.id]?.intent||'';note.placeholder='用户备注（可选）';note.value=choices[r.id]?.note||'';const save=()=>{if(select.value)choices[r.id]={id:r.id,name:r.name,intent:select.value,note:note.value};else delete choices[r.id];localStorage.setItem(storageKey,JSON.stringify(choices));document.getElementById('count').textContent=filtered.length+' 条 / 当前已选 '+Object.keys(choices).length+' 条'};select.onchange=save;note.oninput=save;card.append(title,info,select,note);rows.append(card)}}
q.oninput=draw;view.onchange=draw;document.getElementById('export').onclick=()=>{const blob=new Blob([JSON.stringify({schema_version:1,store_id:D.store_id,expected_revision:D.revision,decisions:Object.values(choices).filter(c=>D.records.some(r=>r.id===c.id)),requires_user_authorization:true},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='memory-decisions.json';a.click();URL.revokeObjectURL(a.href)};draw();</script></html>'''.replace("DATA", embedded, 1)
    output = Path(output)
    if Path(__file__).resolve().parents[1] in output.resolve().parents:
        raise SyncError("PRIVATE_RENDER_IN_CODE_REPOSITORY_FORBIDDEN")
    if output.exists():
        raise SyncError("OUTPUT_EXISTS: choose a new versioned path")
    atomic_write(output, html.encode("utf-8"), overwrite=False)
    return {"ok": True, "saved_path": str(output.resolve()), "records": len(records), "incubation": len(incub)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--archive")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    c = sub.add_parser("catalog"); c.add_argument("--offset", type=int, default=0); c.add_argument("--limit", type=int, default=50)
    c = sub.add_parser("search"); c.add_argument("--query", required=True); c.add_argument("--limit", type=int, default=20); c.add_argument("--semantic-file")
    c = sub.add_parser("history"); c.add_argument("--id", required=True)
    c = sub.add_parser("apply"); c.add_argument("--file", required=True)
    c = sub.add_parser("export"); c.add_argument("--output", required=True)
    c = sub.add_parser("render"); c.add_argument("--output", required=True)
    c = sub.add_parser("import"); c.add_argument("--file", required=True); c.add_argument("--expected-revision", required=True, type=int); c.add_argument("--authorization-file", required=True)
    c = sub.add_parser("migrate"); c.add_argument("--apply", action="store_true"); c.add_argument("--authorization-file"); c.add_argument("--expected-plan-hash")
    c = sub.add_parser("verify-backup"); c.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        root, store = store_for(args)
        if args.command == "migrate":
            # One lock covers reading the legacy source, validation, backup and replacement.
            with locked(store.lock):
                raw = store.path.read_bytes()
                old = load(store.path)
                if old.get("schema_version") == 3:
                    data = validate(old)
                    emit({"ok": True, "duplicate": True, "store_id": data["store_id"], "revision": data["revision"]})
                    return 0
                data = build_migration(root, old)
                validate(data)
                # Store UUID is random; migration identity is source fingerprints and stable record IDs.
                plan = {"source_sha256": __import__("hashlib").sha256(raw).hexdigest(),
                        "snapshot": {k: v for k, v in data.items() if k != "store_id"}}
                plan_hash = digest(plan)
                result = {"ok": True, "mode": "plan", "plan_hash": plan_hash, "records": len(data["records"]),
                          "events": len(data["events"]), "migration": data["migration"]}
                if args.apply:
                    if not args.authorization_file:
                        raise SyncError("MIGRATION_AUTHORIZATION_REQUIRED")
                    authorize({"action": "migrate", "authorization": load(args.authorization_file)})
                    if args.expected_plan_hash != plan_hash:
                        raise SyncError("MIGRATION_PLAN_CHANGED: inspect a fresh read-only plan")
                    backup = store.commit(raw, data)
                    result.update(mode="applied", backup_sha256=backup, saved_path=str(store.path), store_id=data["store_id"])
                emit(result)
                return 0
        data = store.read()
        if args.command == "status":
            emit({"ok": True, "version": VERSION, "schema_version": data["schema_version"], "store_id": data["store_id"],
                  "revision": data["revision"], "records": len(data["records"]), "events": len(data["events"]), "saved_path": str(store.path),
                  "migration": data.get("migration", {})})
        elif args.command == "catalog":
            items = [{k: r[k] for k in ("id", "name", "aliases", "kind", "origin", "depth", "progress", "intent", "evidence_status")}
                     for i, r in data["records"].items() if i not in data["redirects"] and not r.get("rolled_back")]
            emit({"total": len(items), "offset": args.offset, "items": items[args.offset:args.offset + args.limit]})
        elif args.command == "search":
            semantic = load(args.semantic_file) if args.semantic_file else None
            emit({"query": args.query, "results": search(data, args.query, args.limit, semantic),
                  "semantic_mode": "agent_candidates" if semantic is not None else "lexical_only"})
        elif args.command == "history":
            emit(history(data, args.id))
        elif args.command == "apply":
            emit(store.apply(load(args.file)))
        elif args.command == "render":
            emit(render(data, args.output))
        elif args.command == "export":
            dest = Path(args.output).resolve()
            if dest.exists():
                raise SyncError("OUTPUT_EXISTS: choose a new versioned path")
            if Path(__file__).resolve().parents[1] in dest.parents:
                raise SyncError("PRIVATE_EXPORT_IN_CODE_REPOSITORY_FORBIDDEN")
            atomic_write(dest, (canonical(data) + "\n").encode("utf-8"), overwrite=False)
            emit({"ok": True, "saved_path": str(dest), "store_id": data["store_id"], "revision": data["revision"], "sha256": digest(data)})
        elif args.command == "import":
            emit(store.import_document(load(args.file), args.expected_revision, load(args.authorization_file)))
        else:
            import hashlib
            if len(args.sha256) != 64 or any(c not in "0123456789abcdef" for c in args.sha256):
                raise SyncError("INVALID_BACKUP_HASH")
            raw = (store.backups / (args.sha256 + ".json")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != args.sha256:
                raise SyncError("BACKUP_CORRUPT")
            restored = json.loads(raw)
            if restored.get("schema_version") == 3:
                validate(restored)
            emit({"ok": True, "sha256": args.sha256, "schema_version": restored.get("schema_version", "legacy"), "restorable_json": True})
        return 0
    except (SyncError, OSError, ValueError, KeyError, TypeError) as exc:
        emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

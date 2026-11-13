"""Versioned tool access over the existing memory payloads."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .store_coordinator import atomic_json, collection_meta, coordinated, fingerprint, read_json
from .tool_runtime import receipt
from .tool_schemas import CultureValue


class ToolMemory:
    def __init__(self, store, context, known_owners):
        self.store, self.context = store, context
        self.known = {str(uid) for uid in known_owners if uid}
        self.migration = []
        self.errors = {}

    def location(self, target, owner=None):
        if target.startswith("friend_"):
            if not owner or owner not in self.known:
                raise ValueError("OWNER_NOT_IN_SESSION")
            return self.store._friend_path(owner), "info" if target == "friend_info" else "aliases"
        if owner is not None:
            raise ValueError("UNEXPECTED_OWNER")
        filename, field = {"short_term": ("short_term.json", "items"),
                           "long_term": ("long_term.json", "facts"),
                           "culture": ("culture.json", "terms")}[target]
        return self.store._group_dir / filename, field

    async def prepare(self):
        async with coordinated(self.store._group_dir.parent):
            for uid in sorted(self.known):
                try:
                    path = self.store._friend_path(uid)
                    if not path.exists():
                        legacy = self.store._legacy_friend_path(uid)
                        value = await self.store.load_friend(uid)
                        if value is not None and legacy and legacy.exists():
                            self.backup(legacy)
                            self.migration.append({"kind": "friend_namespace", "subject_hash": fingerprint(uid)})
                        value = value or {"id": uid, "nickname": "", "info": [], "aliases": [],
                                          "groups": [self.store.group_id]}
                        await self.store.save_friend(uid, value)
                except (OSError, ValueError, TypeError):
                    self.errors[("friend_info", uid)] = "MEMORY_STORE_UNAVAILABLE"
                    self.errors[("friend_aliases", uid)] = "MEMORY_STORE_UNAVAILABLE"
            targets = [("short_term", None), ("long_term", None), ("culture", None)]
            targets += [(target, uid) for uid in sorted(self.known) for target in ("friend_info", "friend_aliases")]
            for target, uid in targets:
                if (target, uid) in self.errors:
                    continue
                try:
                    path, field = self.location(target, uid)
                    doc = read_json(path)
                    before = fingerprint(doc)
                    collection_meta(doc, field)
                    if fingerprint(doc) != before:
                        self.backup(path)
                        atomic_json(path, doc)
                        self.migration.append({"kind": "entry_identity", "target": target,
                                               "subject_hash": fingerprint(uid) if uid else None})
                except (OSError, ValueError, TypeError):
                    self.errors[(target, uid)] = "MEMORY_STORE_UNAVAILABLE"
            if self.migration:
                atomic_json(self.store._group_dir / "tool_migration.json",
                            {"version": 1, "changes": self.migration})

    @staticmethod
    def backup(path):
        path = Path(path)
        backup = path.with_name(path.name + ".pre-tools.bak")
        if path.exists() and not backup.exists():
            shutil.copyfile(path, backup)

    async def query(self, args):
        if (args.target, args.owner_id) in self.errors:
            return receipt("failed", self.errors[(args.target, args.owner_id)])
        try:
            path, field = self.location(args.target, args.owner_id)
        except ValueError as error:
            return receipt("failed", str(error))
        async with coordinated(self.store._group_dir.parent):
            if not self.context.current:
                return receipt("failed", "SESSION_RESET")
            doc = read_json(path)
            before = fingerprint(doc)
            meta = collection_meta(doc, field)
            if before != fingerprint(doc):
                self.backup(path)
                atomic_json(path, doc)
            entries = [{"entry_id": uid, "value": value}
                       for uid, value in zip(meta["ids"], doc.get(field, []))
                       if args.keyword.casefold() in json.dumps(value, ensure_ascii=False).casefold()]
            selected = []
            for entry in entries[args.offset:args.offset + args.limit]:
                if len(json.dumps(selected + [entry], ensure_ascii=False)) > 12000:
                    if not selected:
                        selected.append({"entry_id": entry["entry_id"], "value_preview": str(entry["value"])[:4000], "truncated": True})
                    break
                selected.append(entry)
            return receipt("ok", "MEMORY_SNAPSHOT", {"target": args.target, "owner_id": args.owner_id,
                           "revision": meta["revision"], "entries": selected, "total": len(entries),
                           "next_offset": args.offset + len(selected) if args.offset + len(selected) < len(entries) else None})

    async def update(self, args):
        if (args.target, args.owner_id) in self.errors:
            return receipt("failed", self.errors[(args.target, args.owner_id)])
        try:
            path, field = self.location(args.target, args.owner_id)
        except ValueError as error:
            return receipt("failed", str(error))
        async with coordinated(self.store._group_dir.parent):
            if not self.context.current:
                return receipt("failed", "SESSION_RESET")
            doc = read_json(path)
            before_meta = fingerprint(doc)
            meta = collection_meta(doc, field)
            if fingerprint(doc) != before_meta:
                self.backup(path)
                atomic_json(path, doc)
            if meta["revision"] != args.expected_revision:
                return receipt("conflict", "MEMORY_REVISION_CONFLICT", {"revision": meta["revision"]})
            values, ids = deepcopy(doc.get(field, [])), list(meta["ids"])
            changes, seen = [], set()
            for op in args.operations:
                if op.entry_id:
                    if op.entry_id in seen or op.entry_id not in ids:
                        return receipt("failed", "MEMORY_ENTRY_CONFLICT")
                    seen.add(op.entry_id)
                if op.action == "delete":
                    index = ids.index(op.entry_id)
                    changes.append({"action": "delete", "entry_id": op.entry_id, "before": values[index]})
                    ids.pop(index)
                    values.pop(index)
                    continue
                if args.target == "culture":
                    if not isinstance(op.value, CultureValue):
                        return receipt("failed", "CULTURE_VALUE_REQUIRED")
                    value = op.value.model_dump()
                    value["term"] = value["term"].strip()
                else:
                    if not isinstance(op.value, str):
                        return receipt("failed", "STRING_VALUE_REQUIRED")
                    value = op.value.strip()
                if op.action == "add":
                    duplicate = any(v == value or args.target == "culture" and v.get("term") == value["term"] for v in values)
                    if duplicate:
                        continue
                    if args.target == "culture":
                        value.update(first_seen=datetime.now().strftime("%Y-%m-%d"), usage_count=1, source="learned")
                    uid = "mem_" + uuid4().hex
                    ids.append(uid)
                    values.append(value)
                    changes.append({"action": "add", "entry_id": uid, "after": value})
                else:
                    index = ids.index(op.entry_id)
                    if args.target == "culture":
                        if any(i != index and v.get("term") == value["term"] for i, v in enumerate(values)):
                            return receipt("failed", "DUPLICATE_CULTURE_TERM")
                        value = {**values[index], **value}
                    if values[index] != value:
                        changes.append({"action": "modify", "entry_id": op.entry_id,
                                        "before": values[index], "after": value})
                        values[index] = value
            if self.context.memory_changes + len(changes) > 40:
                return receipt("failed", "MEMORY_BUDGET_EXCEEDED")
            if not changes:
                return receipt("unchanged", "MEMORY_UNCHANGED", {"revision": meta["revision"]})
            doc[field] = values
            meta.update(ids=ids, revision=uuid4().hex, content_hash=fingerprint(values))
            atomic_json(path, doc)
            self.context.memory_changes += len(changes)
            return receipt("committed", "MEMORY_UPDATED", {"revision": meta["revision"], "changes": changes})

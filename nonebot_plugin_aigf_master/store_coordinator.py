"""Process-local reentrant coordination and atomic JSON replacement."""
import asyncio
from contextlib import asynccontextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary

_LOOPS = WeakKeyDictionary()


@asynccontextmanager
async def coordinated(path):
    loop = asyncio.get_running_loop()
    locks, owners = _LOOPS.setdefault(loop, ({}, {}))
    key = os.path.normcase(str(Path(path).resolve()))
    task = asyncio.current_task()
    if owners.get(key) is task:
        yield
        return
    lock = locks.setdefault(key, asyncio.Lock())
    async with lock:
        owners[key] = task
        try:
            yield
        finally:
            owners.pop(key, None)


def memory_write(func):
    """All memory read-modify-writes share one coordinator, including legacy calls."""
    @wraps(func)
    async def wrapped(self, *args, **kwargs):
        async with coordinated(self._group_dir.parent):
            try:
                return await func(self, *args, **kwargs)
            except (OSError, ValueError, TypeError):
                if func.__name__ in ("update_nickname", "update_card"):
                    from nonebot import logger
                    logger.warning(f"[记忆] 系统资料更新失败: {func.__name__}，保留原文件")
                    return None
                raise
    return wrapped


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    # Never turn a corrupt store into an empty writable store.
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("stored document must be an object")
    return value


def atomic_json(path, data):
    """Small local commits are synchronous so cancellation cannot split replacement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def collection_meta(document, field):
    values = document.get(field, [])
    if not isinstance(values, list):
        raise ValueError(f"Stored {field} is not a list")
    root = document.get("_aigfm_meta")
    if isinstance(root, dict) and root.get("schema_version", 1) != 1:
        raise ValueError("unsupported memory identity schema")
    if not isinstance(root, dict):
        root = {"schema_version": 1, "collections": {}}
        document["_aigfm_meta"] = root
    entries = root.get("collections")
    if not isinstance(entries, dict):
        entries = {}
        root["collections"] = entries
    digest = fingerprint(values)
    meta = entries.get(field)
    valid = (isinstance(meta, dict) and isinstance(meta.get("ids"), list)
             and len(meta["ids"]) == len(values) and all(isinstance(uid, str) and uid for uid in meta["ids"])
             and len(set(meta["ids"])) == len(values) and isinstance(meta.get("revision"), str) and meta["revision"])
    if not valid or meta.get("content_hash") != digest:
        meta = {"revision": uuid4().hex, "ids": ["mem_" + uuid4().hex for _ in values],
                "content_hash": digest}
        entries[field] = meta
    return meta


def commit_collection(path, field, values, ids=None):
    document = read_json(path)
    old = collection_meta(document, field)
    previous = document.get(field, [])
    if ids is None:
        # Legacy writers can preserve references for unchanged unique entries.
        available = list(zip(previous, old["ids"]))
        ids = []
        for value in values:
            match = next((i for i, pair in enumerate(available) if pair[0] == value), None)
            ids.append(available.pop(match)[1] if match is not None else "mem_" + uuid4().hex)
    document[field] = values
    if previous != values or ids != old["ids"]:
        old.update(revision=uuid4().hex, ids=ids, content_hash=fingerprint(values))
    atomic_json(path, document)


def meme_write(func):
    @wraps(func)
    async def wrapped(self, *args, **kwargs):
        async with coordinated(self._data_dir):
            return await func(self, *args, **kwargs)
    return wrapped

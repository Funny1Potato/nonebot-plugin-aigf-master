"""Durable per-batch effects. An unacknowledged intent must never be replayed."""
from pathlib import Path
import time

from .store_coordinator import atomic_json, read_json


class EffectStore:
    def __init__(self, directory: Path, batch_id: str, session_key: str = ""):
        self.path = Path(directory) / (batch_id + ".json")
        self.data = read_json(self.path, {"created": time.time(), "session_key": session_key, "effects": {}})
        if session_key and self.data.get("session_key") not in (None, "", session_key):
            raise ValueError("batch belongs to another session")

    def get(self, key):
        value = self.data["effects"].get(key)
        if value and value["status"] in ("intent", "accepted"):
            return {**value, "status": "uncertain"}
        return value

    def record(self, key, status, digest, **details):
        updated = {**self.data, "effects": dict(self.data["effects"])}
        updated["effects"][key] = {"status": status, "digest": digest, **details}
        atomic_json(self.path, updated)
        self.data = updated

    @staticmethod
    def recovery_notes(directory, session_key, since=0):
        notes = []
        directory = Path(directory)
        if not directory.exists():
            return notes
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = read_json(path)
                if data.get("session_key") != session_key or data.get("created", 0) <= since:
                    continue
                pending = [e for e in data["effects"].values() if e["status"] in ("intent", "uncertain", "accepted")]
                if pending:
                    notes.append(f"历史批次 {path.stem} 有 {len(pending)} 项发送/生图结果未确认；不要自动重放，也不要断言已经失败。")
                if len(notes) >= 20:
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return notes

    @staticmethod
    def cleanup(directory, keep_seconds=7 * 86400):
        directory = Path(directory)
        if not directory.exists():
            return
        for path in directory.glob("*.json"):
            try:
                data = read_json(path)
                if time.time() - float(data["created"]) > keep_seconds:
                    # Keep unresolved delivery evidence; never silently expire it.
                    if not any(e.get("status") in ("intent", "uncertain", "accepted") for e in data["effects"].values()):
                        path.unlink()
            except (OSError, ValueError, KeyError, TypeError):
                continue

"""Read-only migration inventory. Reports counts, never member IDs or memory text."""
import argparse
import hashlib
import json
from pathlib import Path


def inventory(data_dir):
    memory = Path(data_dir) / "memory"
    report = {"collection_files": 0, "metadata_pending": 0, "corrupt_files": 0,
              "legacy_friends": 0, "friend_namespace_pending": 0, "ambiguous_friends": 0, "unsupported_metadata": 0}
    for path in memory.rglob("*.json") if memory.exists() else []:
        if path.name in ("short_term.json", "long_term.json", "culture.json") or "friends" in path.relative_to(memory).parts:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("not an object")
                report["collection_files"] += 1
                meta = data.get("_aigfm_meta")
                fields = {"short_term.json": ["items"], "long_term.json": ["facts"], "culture.json": ["terms"]}.get(path.name, ["info", "aliases"])
                valid = isinstance(meta, dict) and isinstance(meta.get("collections"), dict)
                if isinstance(meta, dict) and meta.get("schema_version", 1) != 1:
                    report["unsupported_metadata"] += 1
                    valid = False
                for field in fields:
                    values = data.get(field, [])
                    if not isinstance(values, list):
                        raise ValueError("invalid collection")
                    state = meta.get("collections", {}).get(field, {}) if valid else {}
                    digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    valid = valid and isinstance(state.get("ids"), list) and len(state["ids"]) == len(values) and state.get("content_hash") == digest
                if not valid:
                    report["metadata_pending"] += 1
                if path.parent == memory / "friends":
                    report["legacy_friends"] += 1
                    slugs = {str(g).split("_", 1)[0] if "_" in str(g) else "onebot11" for g in data.get("groups", [])}
                    if len(slugs) > 1:
                        report["ambiguous_friends"] += 1
                    elif slugs and slugs != {"onebot11"}:
                        slug = next(iter(slugs))
                        digest = hashlib.sha256(json.dumps(str(data.get("id", "")), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                        if not (memory / "friends" / slug / (digest + ".json")).exists():
                            report["friend_namespace_pending"] += 1
            except (OSError, ValueError, TypeError):
                report["corrupt_files"] += 1
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path, help="Plugin data directory, not its memory subdirectory")
    args = parser.parse_args()
    if not args.data_dir.is_dir():
        parser.error("data directory does not exist")
    print(json.dumps(inventory(args.data_dir), ensure_ascii=False, indent=2))

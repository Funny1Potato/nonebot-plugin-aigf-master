"""表情包存储管理"""

import asyncio
import hashlib
import json
import time
from io import BytesIO
from pathlib import Path

import anyio
from nonebot import logger
from PIL import Image

from .models import MemeEntry


class MemeStore:
    """表情包存储"""

    def __init__(self, data_dir: Path, cache_dir: Path):
        self._data_dir = data_dir / "memes"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = cache_dir / "sticker_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._admin_memes: dict[str, MemeEntry] = {}
        self._collected_memes: dict[str, MemeEntry] = {}
        self._recent: list[str] = []
        self._recent_max = 5
        self._cache_index: dict[str, dict] = {}

    @property
    def all_memes(self) -> dict[str, MemeEntry]:
        return {**self._collected_memes, **self._admin_memes}

    async def load_all(self):
        self._admin_memes = await self._load_index(self._data_dir / "memes.json")
        self._collected_memes = await self._load_index(self._data_dir / "collected.json")
        logger.info(
            f"[启动] 表情包加载完成: 管理员 {len(self._admin_memes)} 个, "
            f"自动收集 {len(self._collected_memes)} 个"
        )

    async def _load_index(self, path: Path) -> dict[str, MemeEntry]:
        if not path.exists():
            return {}
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                data = json.loads(await f.read())
            result = {}
            for item in data:
                full_path = self._data_dir / item["path"]
                if full_path.exists():
                    result[item["id"]] = MemeEntry(
                        id=item["id"], filename=item["path"],
                        keywords=item.get("keywords", []),
                        description=item.get("description", ""),
                        usage_count=item.get("usage_count", 0),
                        saved_at=item.get("saved_at", 0.0),
                    )
            return result
        except Exception as e:
            logger.error(f"加载 {path.name} 失败: {e}")
            return {}

    async def _save_collected(self):
        path = self._data_dir / "collected.json"
        data = [
            {"id": m.id, "path": m.filename, "keywords": m.keywords,
             "description": m.description, "usage_count": m.usage_count, "saved_at": m.saved_at}
            for m in self._collected_memes.values()
        ]
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))

    def prompt_list(self) -> str:
        lines = []
        for meme in self.all_memes.values():
            kw = "、".join(meme.keywords)
            lines.append(f"- [{meme.id}] {meme.description}（适用场景：{kw}）")
        return "\n".join(lines)

    def resolve(self, meme_id: str) -> str | None:
        meme = self.all_memes.get(meme_id)
        if not meme or meme_id in self._recent:
            return None
        meme.usage_count += 1
        self._recent.append(meme_id)
        if len(self._recent) > self._recent_max:
            self._recent.pop(0)
        return str(self._data_dir / meme.filename)

    async def persist(self):
        """持久化自动收集表情包索引（含使用次数/保存时间）"""
        await self._save_collected()

    # ========== 缓存管理 ==========

    async def save_to_cache(self, image_bytes: bytes, description: str, emotion: str) -> str:
        cache_id = hashlib.md5(image_bytes).hexdigest()[:12]
        try:
            img_format = await asyncio.to_thread(lambda: Image.open(BytesIO(image_bytes)).format)
        except Exception:
            img_format = None
        ext = "gif" if img_format == "GIF" else "jpg" if img_format in ("JPEG", "JPG") else "png"
        cache_path = self._cache_dir / f"{cache_id}.{ext}"
        if not cache_path.exists():
            async with await anyio.open_file(cache_path, "wb") as f:
                await f.write(image_bytes)
        self._cache_index[cache_id] = {
            "path": str(cache_path), "description": description, "emotion": emotion,
        }
        return cache_id

    def get_cached(self) -> list[dict]:
        return [
            {"id": cid, "description": info["description"], "emotion": info["emotion"]}
            for cid, info in self._cache_index.items()
        ]

    def get_cache_info(self, cache_id: str) -> dict | None:
        return self._cache_index.get(cache_id)

    def clear_cache(self):
        self._cache_index.clear()

    async def save_from_cache(self, cache_id: str, description: str, keywords: list[str]) -> bool:
        cache_info = self._cache_index.get(cache_id)
        if not cache_info:
            return False
        cache_path = Path(cache_info["path"])
        if not cache_path.exists():
            return False
        async with await anyio.open_file(cache_path, "rb") as f:
            image_bytes = await f.read()
        file_hash = hashlib.md5(image_bytes).hexdigest()[:12]
        for meme in self.all_memes.values():
            if file_hash in meme.filename:
                return False
        ext = cache_path.suffix
        filename = f"{file_hash}{ext}"
        dest = self._data_dir / filename
        async with await anyio.open_file(dest, "wb") as f:
            await f.write(image_bytes)
        self._collected_memes[cache_id] = MemeEntry(
            id=cache_id, filename=filename, keywords=keywords, description=description,
            saved_at=time.time(),
        )
        await self._save_collected()
        logger.success(f"[表情包收藏] 已保存: {cache_id} - {description[:30]}")
        await self.cleanup()
        return True

    @staticmethod
    def _cleanup_score(meme: MemeEntry, min_saved: float, max_saved: float, max_usage: int) -> float:
        """归一化加权：越旧且越少用 → 分数越大 → 越优先删"""
        if max_saved > min_saved:
            age_norm = (meme.saved_at - min_saved) / (max_saved - min_saved)
        else:
            age_norm = 0.0
        usage_norm = meme.usage_count / max_usage if max_usage > 0 else 0.0
        oldness = 1.0 - age_norm
        seldom = 1.0 - usage_norm
        return oldness * seldom

    async def cleanup(self, max_count: int = 0):
        """自动收集的表情包超过上限时清理。优先删除"旧且用得少"的，保留最近发过的"""
        from .config import plugin_config
        if max_count <= 0:
            max_count = plugin_config.aigfm_meme_max_count
        if len(self._collected_memes) <= max_count:
            return
        to_delete = len(self._collected_memes) - max_count
        recent_ids = set(self._recent)
        memes = list(self._collected_memes.values())
        min_saved = min((m.saved_at for m in memes), default=0.0)
        max_saved = max((m.saved_at for m in memes), default=0.0)
        max_usage = max((m.usage_count for m in memes), default=0)
        # 主键：分数大(旧且少用)优先删；次级：少用优先、旧优先
        sorted_memes = sorted(
            memes,
            key=lambda m: (-self._cleanup_score(m, min_saved, max_saved, max_usage),
                           m.usage_count, m.saved_at),
        )
        candidates = [m for m in sorted_memes if m.id not in recent_ids] + \
                     [m for m in sorted_memes if m.id in recent_ids]
        to_remove = candidates[:to_delete]
        for meme in to_remove:
            meme_path = self._data_dir / meme.filename
            if meme_path.exists():
                meme_path.unlink()
            self._collected_memes.pop(meme.id, None)
        await self._save_collected()
        logger.info(f"[清理] 表情包清理: {len(to_remove)} 个")

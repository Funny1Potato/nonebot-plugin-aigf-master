"""LLM 图片库：收藏图 + 头像识别图（统一库，平时不注入 prompt，按需工具访问）"""

import json
import time
from pathlib import Path

import anyio
import httpx
from nonebot import logger


class ImageLibraryStore:
    """LLM 图片库

    存储：
      {data_dir}/image_library/library.json   索引（条目列表）
      {cache_dir}/image_library/{id}.{ext}    图片文件
    条目：{id, category: "collect"|"avatar", description, emotion, user_id, saved_at, usage_count}
    清理与表情包相同：超 max_count 时按权重（旧且少用 → 优先删），最近使用保底。
    """

    def __init__(self, data_dir: Path, cache_dir: Path, max_count: int = 100):
        self._data_dir = data_dir / "image_library"
        self._cache_dir = cache_dir / "image_library"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict] = {}
        self._max_count = max_count
        self._recent: list[str] = []
        self._recent_max = 5
        # 同步加载索引（模块生命周期内一次性，简单可靠）
        path = self._data_dir / "library.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("id"):
                            self._index[item["id"]] = item
            except Exception as e:
                logger.error(f"[图片库] 加载索引失败: {e}")

    async def _save(self):
        path = self._data_dir / "library.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(list(self._index.values()), ensure_ascii=False, indent=2))

    async def get_entry(self, entry_id: str) -> dict | None:
        return self._index.get(entry_id)

    async def get_file(self, entry_id: str) -> str | None:
        """条目存在且图片文件存在 → 返回文件路径"""
        if entry_id not in self._index:
            return None
        for p in self._cache_dir.glob(f"{entry_id}.*"):
            if p.is_file():
                return str(p)
        return None

    async def save_image(self, entry_id: str, image_bytes: bytes, ext: str,
                         description: str = "", emotion: str = "",
                         category: str = "collect", user_id: str = "") -> dict:
        """保存/更新图片条目（先写文件，再更新索引并触发清理）"""
        file_path = self._cache_dir / f"{entry_id}.{ext.lstrip('.')}"
        async with await anyio.open_file(file_path, "wb") as f:
            await f.write(image_bytes)
            # 写文件后删除旧格式文件（同 id 不同扩展名）
        for old in self._cache_dir.glob(f"{entry_id}.*"):
            if old != file_path:
                try:
                    old.unlink()
                except OSError:
                    pass
        entry = self._index.get(entry_id) or {}
        entry.update({
            "id": entry_id, "category": category, "description": description,
            "emotion": emotion, "user_id": user_id,
            "saved_at": entry.get("saved_at", time.time()),
            "usage_count": entry.get("usage_count", 0),
        })
        self._index[entry_id] = entry
        await self._save()
        await self._cleanup()
        return entry

    async def update_entry(self, entry_id: str, description=None, emotion=None) -> bool:
        entry = self._index.get(entry_id)
        if not entry:
            return False
        if description is not None:
            entry["description"] = description
        if emotion is not None:
            entry["emotion"] = emotion
        await self._save()
        return True

    async def touch(self, entry_id: str):
        """使用计次（查询/发送时调用），最近使用保底"""
        entry = self._index.get(entry_id)
        if not entry:
            return
        entry["usage_count"] = entry.get("usage_count", 0) + 1
        if entry_id not in self._recent:
            self._recent.append(entry_id)
            if len(self._recent) > self._recent_max:
                self._recent.pop(0)
        await self._save()

    async def find(self, keyword: str = "", limit: int = 15) -> list[dict]:
        """按描述/情感/类别过滤；空关键词返回最近保存的条目"""
        entries = list(self._index.values())
        if keyword:
            kw = keyword.lower()
            entries = [e for e in entries
                       if kw in (e.get("description", "") or "").lower()
                       or kw in (e.get("emotion", "") or "").lower()]
        entries.sort(key=lambda e: e.get("saved_at", 0.0), reverse=True)
        return entries[:limit]

    async def count(self) -> int:
        return len(self._index)

    @staticmethod
    def avatar_url(user_id: str) -> str:
        """QQ 头像图床 URL"""
        return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

    async def fetch_avatar(self, user_id: str) -> bytes | None:
        """下载头像图片 bytes"""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(self.avatar_url(user_id))
            resp.raise_for_status()
            return resp.content

    async def _cleanup(self):
        while len(self._index) > max(self._max_count, 1):
            recent_ids = set(self._recent)
            entries = list(self._index.values())
            min_saved = min((e.get("saved_at", 0.0) for e in entries), default=0.0)
            max_saved = max((e.get("saved_at", 0.0) for e in entries), default=0.0)
            max_usage = max((e.get("usage_count", 0) for e in entries), default=0)

            def score(e):
                age_norm = (e.get("saved_at", 0.0) - min_saved) / (max_saved - min_saved) if max_saved > min_saved else 0.0
                usage_norm = e.get("usage_count", 0) / max_usage if max_usage > 0 else 0.0
                return (1.0 - age_norm) * (1.0 - usage_norm)

            sorted_entries = sorted(
                entries,
                key=lambda e: (-score(e), e.get("usage_count", 0), e.get("saved_at", 0.0)),
            )
            candidates = [e for e in sorted_entries if e.get("id") not in recent_ids] + \
                         [e for e in sorted_entries if e.get("id") in recent_ids]
            if not candidates:
                break
            victim = candidates[0]
            vid = victim.get("id")
            for p in self._cache_dir.glob(f"{vid}.*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            self._index.pop(vid, None)
        await self._save()
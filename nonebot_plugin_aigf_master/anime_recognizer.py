"""二次元角色识别客户端：调用独立部署的 WD14 推理服务（aigfm-anime-recognize）"""

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx
from nonebot import logger

from .config import plugin_config


@dataclass
class AnimeResult:
    """角色识别结果（服务返回）"""
    chars: list = field(default_factory=list)  # [(名称, 置信度), ...]，触发成功但无命中为空列表
    rating: dict = field(default_factory=dict)  # {"general","sensitive","questionable","explicit"}
    people_count: int | None = None


class AnimeRecognizer:
    """WD14 角色识别 HTTP 客户端（结果按图片 md5 缓存到 image_cache）"""

    MARKER = "（二次元）"

    def __init__(self, cache_dir: Path, timeout: float = 15.0):
        self._cache_dir = cache_dir / "image_cache"
        self._timeout = timeout

    def enabled(self) -> bool:
        return bool(
            plugin_config.aigfm_anime_recognize_enabled
            and plugin_config.aigfm_anime_recognize_url
        )

    def looks_anime(self, description: str) -> bool:
        """VLM 描述里带了（二次元）标记才认为需要角色识别"""
        return self.MARKER in (description or "")

    def _cache_file(self, image_bytes: bytes) -> Path:
        digest = hashlib.md5(image_bytes).hexdigest()
        # 注意：与 VLM 描述缓存（{md5}_anime.json）区分开，避免同名字段结构冲突
        return self._cache_dir / f"{digest}_anime_recognize.json"

    async def maybe_recognize(self, image_bytes: bytes, description: str) -> AnimeResult | None:
        """开关关 / 描述无标记 → None；缓存命中 → 返回缓存；服务失败 → 降级 None。"""
        if not self.enabled() or not self.looks_anime(description):
            return None

        cache_file = self._cache_file(image_bytes)
        if cache_file.exists():
            try:
                async with await anyio.open_file(cache_file, encoding="utf-8") as f:
                    data = json.loads(await f.read())
                logger.debug(f"[角色识别] 命中缓存: {cache_file.name}")
                return AnimeResult(
                    chars=[tuple(x) for x in data.get("chars") or []],
                    rating=data.get("rating") or {},
                    people_count=data.get("people_count"),
                )
            except Exception:
                cache_file.unlink(missing_ok=True)

        result = await self._request(image_bytes)
        if result is None:
            return None
        try:
            async with await anyio.open_file(cache_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps({
                    "chars": [[name, score] for name, score in result.chars],
                    "rating": result.rating,
                    "people_count": result.people_count,
                }, ensure_ascii=False))
        except Exception:
            pass
        return result

    async def _request(self, image_bytes: bytes) -> AnimeResult | None:
        url = plugin_config.aigfm_anime_recognize_url.rstrip("/") + "/recognize"
        headers = {}
        token = plugin_config.aigfm_anime_recognize_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {"image": base64.b64encode(image_bytes).decode()}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"[角色识别] 服务调用失败: {e}")
            return None
        chars = [(str(name), float(score)) for name, score in data.get("characters") or []]
        rating = data.get("rating") or {}
        people_count = data.get("people_count")
        return AnimeResult(chars=chars, rating=rating, people_count=people_count)

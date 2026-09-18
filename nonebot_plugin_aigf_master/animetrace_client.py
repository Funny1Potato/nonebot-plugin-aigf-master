"""AnimeTrace 角色识别客户端（公共 API，无需鉴权）

文档：https://ai.animedb.cn/zh/api-docs
与本地 WD14 服务不同：AnimeTrace 不给数值置信度，只给「每个检测框的 not_confident 布尔 + 候选列表（越靠前越可能）」，
因此命中取「not_confident 为假」的框的候选首位，并带上作品名；模型列表会动态增减，故不写死模型名
（启动时查一次 /v1/model/list 取 default，服务报参数错时重查一次）。
"""

import asyncio
import io
import json
from pathlib import Path

import anyio
import httpx
from nonebot import logger
from PIL import Image

from .config import plugin_config
from .models import AnimeHit, AnimeResult, anime_hits_from_json, anime_hits_to_json


def _shrink_jpeg(image_bytes: bytes, max_edge: int = 1280) -> bytes | None:
    """压成较小 JPEG（长边不超过 max_edge），供服务提示「图片过大」时重试；失败返回 None。"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.seek(0)
        frame = img.convert("RGB")
        if max(frame.size) > max_edge:
            frame.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        frame.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None


class AnimeTraceClient:
    """AnimeTrace HTTP 客户端（结果按图片 md5 缓存到 image_cache）"""

    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir / "image_cache"
        self._model: str | None = None    # 识别用模型 id；None = 不带 model 参数（服务用自身默认）
        self._model_loaded = False
        self._lock = anyio.Lock()

    async def ensure_model(self) -> str | None:
        """取识别模型 id（进程内只查一次）；查询失败返回 None（调用时不带 model，服务用默认）"""
        if self._model_loaded:
            return self._model
        async with self._lock:
            if not self._model_loaded:
                self._model = await self._fetch_model()
                self._model_loaded = True
        return self._model

    async def recognize(self, digest: str, payload: bytes) -> AnimeResult | None:
        """识别一张图。None = 调用失败（静默降级）；AnimeResult.hits 为空 = 成功但无可信结果。"""
        cache_file = self._cache_dir / f"{digest}_animetrace.json"
        if cache_file.exists():
            try:
                async with await anyio.open_file(cache_file, encoding="utf-8") as f:
                    data = json.loads(await f.read())
                logger.debug(f"[角色识别] AnimeTrace 命中缓存: {cache_file.name}")
                return AnimeResult(
                    hits=anime_hits_from_json(data.get("hits")) or [],
                    source="animetrace",
                    people_count=data.get("people_count"),
                )
            except Exception:
                cache_file.unlink(missing_ok=True)

        result = await self._search(payload)
        if result is None:
            return None
        try:
            async with await anyio.open_file(cache_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps({
                    "hits": anime_hits_to_json(result.hits),
                    "people_count": result.people_count,
                }, ensure_ascii=False))
        except Exception:
            pass
        return result

    def _base_url(self) -> str:
        return plugin_config.aigfm_animetrace_url.rstrip("/")

    def _proxy(self) -> str | None:
        if not plugin_config.aigfm_proxy_enabled:
            return None
        return plugin_config.aigfm_https_proxy or plugin_config.aigfm_http_proxy or None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=float(plugin_config.aigfm_animetrace_timeout or 20.0),
            proxy=self._proxy(),
        )

    async def _fetch_model(self) -> str | None:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base_url()}/v1/model/list")
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"[角色识别] AnimeTrace 模型列表获取失败: {e}（改用服务默认模型）")
            return None
        models = [m for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
        usable = [m for m in models if m.get("enabled", True)]
        if not usable:
            logger.warning("[角色识别] AnimeTrace 模型列表无可选项，改用服务默认模型")
            return None
        chosen = next((m for m in usable if m.get("default")), usable[0])
        logger.info(f"[角色识别] AnimeTrace 模型: {chosen['id']}")
        return str(chosen["id"])

    async def _search(self, payload: bytes, allow_retry: bool = True) -> AnimeResult | None:
        form = {"is_multi": "1", "ai_detect": "0"}
        model = await self.ensure_model()
        if model:
            form["model"] = model
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self._base_url()}/v1/search",
                    data=form, files={"file": ("image.jpg", payload, "image/jpeg")},
                )
                if allow_retry and resp.status_code == 413:
                    shrunk = await asyncio.to_thread(_shrink_jpeg, payload)
                    if shrunk and shrunk != payload:
                        logger.info("[角色识别] AnimeTrace 提示图片过大，压缩后重试")
                        return await self._search(shrunk, allow_retry=False)
                if allow_retry and resp.status_code in (400, 422):
                    logger.info(f"[角色识别] AnimeTrace 拒绝参数（HTTP {resp.status_code}），重查模型后重试")
                    self._model_loaded, self._model = False, None
                    return await self._search(payload, allow_retry=False)
                resp.raise_for_status()
                body = resp.json()
        except Exception as e:
            logger.warning(f"[角色识别] AnimeTrace 调用失败: {e}")
            return None

        if not isinstance(body, dict):
            logger.warning("[角色识别] AnimeTrace 返回内容不是对象")
            return None
        code = body.get("code")
        if code not in (0, 200, 17720):
            if allow_retry and code == 17703:
                logger.info("[角色识别] AnimeTrace 报参数错误，重查模型后重试")
                self._model_loaded, self._model = False, None
                return await self._search(payload, allow_retry=False)
            logger.warning(f"[角色识别] AnimeTrace 返回异常: code={code}, message={body.get('message')}")
            return None
        return self._parse(body)

    @staticmethod
    def _parse(body: dict) -> AnimeResult:
        boxes = [b for b in (body.get("data") or []) if isinstance(b, dict)]
        hits: list[AnimeHit] = []
        low: list[AnimeHit] = []
        for box in boxes:
            candidates = [c for c in (box.get("character") or [])
                          if isinstance(c, dict) and c.get("character")]
            if not candidates:
                continue
            top = AnimeHit(str(candidates[0]["character"]), None,
                           str(candidates[0].get("work") or ""))
            (low if box.get("not_confident") else hits).append(top)
        if not hits and low:
            # 只在日志里给出服务给的最可能候选，便于排查「为什么显示未能识别」
            preview = "、".join(f"{h.name}（{h.work}）" if h.work else h.name for h in low[:3])
            logger.info(f"[角色识别] AnimeTrace 无可信结果，低置信候选: {preview}")
        return AnimeResult(hits=hits, source="animetrace",
                           people_count=len(boxes) if boxes else None)

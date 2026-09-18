"""二次元角色识别：本地 WD14 推理服务与 AnimeTrace 公共 API 的调度

后端由 aigfm_anime_backend 选择；both 模式下先问本地服务，本地置信度不足再问 AnimeTrace。
两边都没有可信结果 → 按「未能识别」处理；全部后端调用失败 → 静默降级（不渲染识别段）。
"""

import base64
import hashlib
import json
from pathlib import Path

import anyio
import httpx
from nonebot import logger

from .animetrace_client import AnimeTraceClient
from .config import plugin_config
from .models import AnimeHit, AnimeResult, anime_hits_from_json, anime_hits_to_json

LOCAL = "anime-recognize"
TRACE = "animetrace"


def effective_backend() -> str:
    """当前实际生效的后端：off / anime-recognize / animetrace / both

    旧开关 aigfm_anime_recognize_enabled 仍兼容（backend=off 但旧开关为 true 时按本地后端处理）；
    缺 URL 的一侧会被摘掉（both 退化为单后端，单后端缺 URL 则视为 off）。
    """
    backend = plugin_config.aigfm_anime_backend
    if backend == "off" and plugin_config.aigfm_anime_recognize_enabled:
        backend = LOCAL
    if backend in (LOCAL, "both") and not plugin_config.aigfm_anime_recognize_url:
        backend = TRACE if backend == "both" else "off"
    if backend in (TRACE, "both") and not plugin_config.aigfm_animetrace_url:
        backend = LOCAL if backend == "both" else "off"
    return backend


def _confident_hits(hits: list[AnimeHit]) -> list[AnimeHit]:
    """只保留达到置信度阈值的命中（AnimeTrace 的命中没有分数，已由服务裁定，直接保留）"""
    threshold = plugin_config.aigfm_anime_recognize_min_confidence
    return [h for h in hits if h.score is None or h.score >= threshold]


def _format_hits(hits: list[AnimeHit], limit: int = 3) -> str:
    parts = []
    for h in hits[:limit]:
        if h.work:
            parts.append(f"{h.name}（{h.work}）")
        elif h.score is not None:
            parts.append(f"{h.name} {h.score:.1%}")
        else:
            parts.append(h.name)
    return ", ".join(parts)


class AnimeRecognizeClient:
    """本地 WD14 推理服务客户端（aigfm-anime-recognize，结果按图片 md5 缓存）"""

    def __init__(self, cache_dir: Path, timeout: float = 15.0):
        self._cache_dir = cache_dir / "image_cache"
        self._timeout = timeout

    async def recognize(self, digest: str, payload: bytes) -> AnimeResult | None:
        """识别一张图。None = 调用失败（静默降级）；hits 是原始候选，阈值由调用方裁定。"""
        cache_file = self._cache_dir / f"{digest}_anime_recognize.json"
        if cache_file.exists():
            try:
                async with await anyio.open_file(cache_file, encoding="utf-8") as f:
                    data = json.loads(await f.read())
                logger.debug(f"[角色识别] 本地服务命中缓存: {cache_file.name}")
                return AnimeResult(
                    hits=anime_hits_from_json(data.get("hits", data.get("chars"))) or [],
                    source=LOCAL,
                    rating=data.get("rating") or {},
                    people_count=data.get("people_count"),
                )
            except Exception:
                cache_file.unlink(missing_ok=True)

        headers = {}
        token = plugin_config.aigfm_anime_recognize_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = plugin_config.aigfm_anime_recognize_url.rstrip("/") + "/recognize"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url, headers=headers,
                    json={"image": base64.b64encode(payload).decode()},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"[角色识别] 本地服务调用失败: {e}")
            return None

        result = AnimeResult(
            hits=[AnimeHit(str(n), float(s)) for n, s in data.get("characters") or []],
            source=LOCAL,
            rating=data.get("rating") or {},
            people_count=data.get("people_count"),
        )
        try:
            async with await anyio.open_file(cache_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps({
                    "hits": anime_hits_to_json(result.hits),
                    "rating": result.rating,
                    "people_count": result.people_count,
                }, ensure_ascii=False))
        except Exception:
            pass
        return result


class AnimeRecognizer:
    """角色识别调度器：按生效后端调用本地服务 / AnimeTrace，并做「本地 → AnimeTrace」降级

    maybe_recognize 返回 None = 未启用 / 描述无（二次元）标记 / 全部后端调用失败（静默降级）；
    返回的 hits 为空 = 有后端成功应答但无可信结果（渲染「未能识别」）。
    """

    MARKER = "（二次元）"

    def __init__(self, cache_dir: Path):
        self._local = AnimeRecognizeClient(cache_dir)
        self._trace = AnimeTraceClient(cache_dir)

    def backend(self) -> str:
        return effective_backend()

    def enabled(self) -> bool:
        return self.backend() != "off"

    def looks_anime(self, description: str) -> bool:
        """VLM 描述里带了（二次元）标记才认为需要角色识别"""
        return self.MARKER in (description or "")

    async def warmup(self) -> None:
        """启动预热：先查一次 AnimeTrace 模型列表（失败静默，调用时退回服务默认模型）"""
        try:
            if self.backend() in (TRACE, "both"):
                await self._trace.ensure_model()
        except Exception:
            pass

    async def maybe_recognize(self, image_bytes: bytes, description: str,
                              payload: bytes | None = None) -> AnimeResult | None:
        """识别一张图。payload = 真正上传的字节（动图传首帧 JPEG），缓存键始终用原图 md5。"""
        backend = self.backend()
        if backend == "off" or not self.looks_anime(description):
            return None
        digest = hashlib.md5(image_bytes).hexdigest()
        upload = payload or image_bytes

        local: AnimeResult | None = None
        if backend in (LOCAL, "both"):
            local = await self._local.recognize(digest, upload)
            if local is not None:
                hits = _confident_hits(local.hits)
                if hits:
                    logger.info(f"[角色识别] 命中（本地 WD14）: {_format_hits(hits)}")
                    return AnimeResult(hits=hits, source=LOCAL,
                                       rating=local.rating, people_count=local.people_count)

        if backend in (TRACE, "both"):
            if local is not None:
                logger.info("[角色识别] 本地 WD14 置信度不足，改用 AnimeTrace")
            trace = await self._trace.recognize(digest, upload)
            if trace is not None:
                if trace.hits:
                    logger.info(f"[角色识别] 命中（AnimeTrace）: {_format_hits(trace.hits)}")
                    return AnimeResult(hits=trace.hits, source=TRACE,
                                       people_count=trace.people_count)
                logger.info("[角色识别] 未能识别（AnimeTrace 低置信）")
                # 人数优先用本地服务的统计（AnimeTrace 的检测框可能有误检）
                people = local.people_count if local is not None and local.people_count else trace.people_count
                return AnimeResult(hits=[], rating=local.rating if local is not None else {},
                                   people_count=people)
            if local is None:
                logger.warning("[角色识别] 本地服务与 AnimeTrace 均不可用，静默降级")
                return None
            logger.info("[角色识别] 未能识别（本地置信度不足，AnimeTrace 不可用）")
            return AnimeResult(hits=[], rating=local.rating, people_count=local.people_count)

        if local is None:
            return None
        logger.info("[角色识别] 未能识别（本地 WD14 置信度不足）")
        return AnimeResult(hits=[], rating=local.rating, people_count=local.people_count)

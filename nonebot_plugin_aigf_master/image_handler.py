"""图片处理"""

import asyncio
import base64
import hashlib
import io
from pathlib import Path

import anyio
from nonebot import logger
import numpy as np
from openai import APITimeoutError
from PIL import Image

from .anime_recognizer import AnimeRecognizer
from .cache_cleanup import prune_dir
from .config import plugin_config
from .llm_client import make_http_client
from .models import AnimeHit, ImageInfo, anime_hits_from_json, anime_hits_to_json
from .vlm_client import VLMClient


class ImageHandler:
    """图片处理：VLM 描述 + 缓存"""

    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir / "image_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._anime = AnimeRecognizer(cache_dir)
        self._vlm: VLMClient | None = None
        if plugin_config.aigfm_image_mode == "vlm" and plugin_config.aigfm_vlm_enabled:
            if not plugin_config.aigfm_vlm_model or not plugin_config.aigfm_vlm_base_url:
                raise ValueError("VLM 模式已启用，但 vlm_model 或 vlm_base_url 未配置")
            api_key = plugin_config.aigfm_vlm_api_key or plugin_config.aigfm_llm_api_key
            proxy = (plugin_config.aigfm_https_proxy or plugin_config.aigfm_http_proxy) if plugin_config.aigfm_proxy_enabled else None
            self._vlm = VLMClient(
                api_key=api_key, model=plugin_config.aigfm_vlm_model,
                base_url=plugin_config.aigfm_vlm_base_url,
                http_client=make_http_client(proxy),
            )

    async def warmup_anime(self) -> None:
        """启动预热角色识别后端（目前仅有 AnimeTrace 的模型列表查询）"""
        await self._anime.warmup()

    async def describe(self, image_base64: str, is_sticker: bool) -> ImageInfo | None:
        if not self._vlm:
            logger.warning("[VLM] VLM 未启用，跳过图片分析")
            return None

        image_bytes = base64.b64decode(image_base64)
        image_hash = hashlib.md5(image_bytes).hexdigest()
        anime_on = self._anime.enabled()
        # 开关开启时 VLM 描述带（二次元）标记要求，结果单独缓存，避免旧缓存无标记导致永不触发识别
        cache_file = self._cache_dir / f"{image_hash}{'_anime' if anime_on else ''}.json"

        if cache_file.exists():
            try:
                async with await anyio.open_file(cache_file, encoding="utf-8") as f:
                    import json
                    data = json.loads(await f.read())
                logger.debug(f"[VLM] 命中缓存: hash={image_hash[:8]}")
                # 兼容旧缓存字段名（anime_chars：[[标签, 置信度], ...]）
                raw_hits = data.get("anime_hits", data.get("anime_chars"))
                return ImageInfo(
                    description=data.get("description", ""),
                    emotion=data.get("emotion", ""),
                    is_sticker=data.get("is_sticker", False),
                    anime_hits=anime_hits_from_json(raw_hits),
                    anime_rating=data.get("anime_rating") or {},
                    anime_people_count=data.get("anime_people_count"),
                )
            except Exception:
                cache_file.unlink(missing_ok=True)

        image_format = await asyncio.to_thread(lambda: Image.open(io.BytesIO(image_bytes)).format)
        if not image_format:
            return None

        anime_payload: bytes | None = None
        if image_format.upper() == "GIF":
            gif_b64 = await asyncio.to_thread(_transform_gif, image_base64)
            if not gif_b64:
                return None
            payload, desc_format = gif_b64, "jpeg"
            # 动图统一用首帧 JPEG 送角色识别（本地服务内部也是取首帧；AnimeTrace 不支持 GIF）
            anime_payload = await asyncio.to_thread(_first_frame_jpeg, image_bytes)
            desc_prompt = "用中文简短描述这张动态图的内容。如果图中有人物，只描述外貌特征，不要识别角色。若有文字请描述。"
        else:
            payload, desc_format = image_base64, image_format.lower()
            desc_prompt = "用中文简短描述这张图片的内容。如果图中有人物，只描述外貌特征，不要识别角色。若有文字请描述。"
        if anime_on:
            desc_prompt += "如果这是动漫、漫画、游戏立绘等二次元风格，请在描述末尾加上（二次元）标记。"

        # 情感只用于贴纸的展示与收藏判断，普通图片拿到的情感不进 prompt → 省掉这次往返；
        # 剩下的请求并发发出，识图耗时从两次串行压成一次
        requests = [self._vlm.request(desc_prompt, payload, desc_format)]
        if is_sticker:
            requests.append(self._vlm.request(
                "用3个词概括这个表情包的情感，用顿号分隔，如：开心、得意、搞笑", payload, "jpeg"))
        # 不做外层 wait_for 掐断：让 VLM 请求跑到自身超时（VLMClient 写死 60s）为止，
        # 超时按识图失败处理；其它异常照旧抛出（由调用方落 [图片加载失败]）
        try:
            results = await asyncio.gather(*requests, return_exceptions=True)
        except Exception:
            raise

        for r in results:
            if isinstance(r, APITimeoutError):
                logger.error(f"[VLM] 识图超时: hash={image_hash[:8]}, 上限 60s（VLM 请求自身超时）")
                return None
            if isinstance(r, Exception):
                raise r

        desc, emo = results[0], (results[1] if len(results) > 1 else "")
        if not desc or (is_sticker and not emo):
            return None

        logger.info(f"[VLM] 描述: {desc[:80]}, 情感: {emo}")
        result = ImageInfo(description=desc, emotion=emo, is_sticker=is_sticker)
        anime = await self._anime.maybe_recognize(image_bytes, desc, anime_payload)
        if anime is not None:
            result.anime_hits = anime.hits
            result.anime_rating = anime.rating
            result.anime_people_count = anime.people_count
        import json
        async with await anyio.open_file(cache_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps({
                "description": desc,
                "emotion": emo,
                "is_sticker": is_sticker,
                "anime_hits": anime_hits_to_json(anime.hits) if anime is not None else None,
                "anime_rating": anime.rating if anime is not None else None,
                "anime_people_count": anime.people_count if anime is not None else None,
            }, ensure_ascii=False))
        # 描述缓存与角色识别缓存都只增不减，写入后按上限清一次（含角色识别客户端刚写的文件）
        prune_dir(self._cache_dir, plugin_config.aigfm_image_cache_max_files, "*.json")
        return result


def _first_frame_jpeg(image_bytes: bytes, max_edge: int = 1280) -> bytes | None:
    """GIF 首帧转 JPEG（长边不超过 max_edge）供角色识别上传；失败返回 None（调用方退回原图）。"""
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


def _transform_gif(gif_base64: str, max_frames: int = 15) -> str | None:
    try:
        gif_data = base64.b64decode(gif_base64)
        gif = Image.open(io.BytesIO(gif_data))
        all_frames = []
        try:
            while True:
                gif.seek(len(all_frames))
                all_frames.append(gif.convert("RGB").copy())
        except EOFError:
            pass
        if not all_frames:
            return None
        selected = [all_frames[0]]
        for frame in all_frames[1:]:
            mse = np.mean((np.array(frame) - np.array(selected[-1])) ** 2)
            if mse > 1000:
                selected.append(frame)
            if len(selected) >= max_frames:
                break
        target_h = 200
        w, h = selected[0].size
        target_w = max(1, int((target_h / h) * w)) if h > 0 else 1
        resized = [f.resize((target_w, target_h), Image.Resampling.LANCZOS) for f in selected]
        combined = Image.new("RGB", (target_w * len(resized), target_h))
        for i, f in enumerate(resized):
            combined.paste(f, (i * target_w, 0))
        buf = io.BytesIO()
        combined.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _format_anime_hit(hit: AnimeHit) -> str:
    """AnimeTrace 带作品名 → 「角色（作品）」；本地 WD14 → 「标签 98.7%」"""
    if hit.work:
        return f"{hit.name}（{hit.work}）"
    if hit.score is not None:
        return f"{hit.name} {hit.score:.1%}"
    return hit.name


def anime_section_text(info: ImageInfo) -> str:
    """角色识别渲染段；anime_hits 为 None（未触发/全部后端失败）时返回空串（静默降级）。"""
    if info.anime_hits is None:
        return ""
    filtered = [h for h in info.anime_hits
                if h.score is None or h.score >= plugin_config.aigfm_anime_recognize_min_confidence]
    shown = filtered[:plugin_config.aigfm_anime_recognize_max_characters]
    pc = info.anime_people_count
    bits = []
    if (info.anime_rating or {}).get("explicit", 0.0) >= plugin_config.aigfm_anime_nsfw_threshold:
        bits.append("[NSFW]")
    if shown:
        bits.append("[角色识别: " + ", ".join(_format_anime_hit(h) for h in shown) + "]")
        if pc and pc >= 2 and len(shown) < pc:
            bits.append(f"（图中检测到 {pc} 个角色，仅识别出部分）")
    else:
        if pc and pc >= 2:
            bits.append(f"[角色识别: 未能识别]（图中检测到 {pc} 个角色）")
        else:
            bits.append("[角色识别: 未能识别]")
    return " " + " ".join(bits)

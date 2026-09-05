"""图片处理"""

import asyncio
import base64
import hashlib
import io
from pathlib import Path

import anyio
from nonebot import logger
import numpy as np
from PIL import Image

from .config import plugin_config
from .llm_client import make_http_client
from .models import ImageInfo
from .vlm_client import VLMClient


class ImageHandler:
    """图片处理：VLM 描述 + 缓存"""

    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir / "image_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
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

    async def describe(self, image_base64: str, is_sticker: bool) -> ImageInfo | None:
        if not self._vlm:
            logger.warning("[VLM] VLM 未启用，跳过图片分析")
            return None

        image_bytes = base64.b64decode(image_base64)
        image_hash = hashlib.md5(image_bytes).hexdigest()
        cache_file = self._cache_dir / f"{image_hash}.json"

        if cache_file.exists():
            try:
                async with await anyio.open_file(cache_file, encoding="utf-8") as f:
                    import json
                    data = json.loads(await f.read())
                logger.debug(f"[VLM] 命中缓存: hash={image_hash[:8]}")
                return ImageInfo(**data)
            except Exception:
                cache_file.unlink(missing_ok=True)

        image_format = await asyncio.to_thread(lambda: Image.open(io.BytesIO(image_bytes)).format)
        if not image_format:
            return None

        if image_format.upper() == "GIF":
            gif_b64 = await asyncio.to_thread(_transform_gif, image_base64)
            if not gif_b64:
                return None
            payload, desc_format = gif_b64, "jpeg"
            desc_prompt = "用中文简短描述这张动态图的内容。如果图中有人物，只描述外貌特征，不要识别角色。若有文字请描述。"
        else:
            payload, desc_format = image_base64, image_format.lower()
            desc_prompt = "用中文简短描述这张图片的内容。如果图中有人物，只描述外貌特征，不要识别角色。若有文字请描述。"

        # 情感只用于贴纸的展示与收藏判断，普通图片拿到的情感不进 prompt → 省掉这次往返；
        # 剩下的请求并发发出，识图耗时从两次串行压成一次
        requests = [self._vlm.request(desc_prompt, payload, desc_format)]
        if is_sticker:
            requests.append(self._vlm.request(
                "用3个词概括这个表情包的情感，用顿号分隔，如：开心、得意、搞笑", payload, "jpeg"))
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*requests), timeout=plugin_config.aigfm_incomplete_timeout)
        except asyncio.TimeoutError:
            logger.error(f"[VLM] 识图超时: hash={image_hash[:8]}, 上限 {int(plugin_config.aigfm_incomplete_timeout)}s")
            return None

        desc, emo = results[0], (results[1] if len(results) > 1 else "")
        if not desc or (is_sticker and not emo):
            return None

        logger.info(f"[VLM] 描述: {desc[:80]}, 情感: {emo}")
        result = ImageInfo(description=desc, emotion=emo, is_sticker=is_sticker)
        import json
        async with await anyio.open_file(cache_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"description": desc, "emotion": emo, "is_sticker": is_sticker}, ensure_ascii=False))
        return result


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

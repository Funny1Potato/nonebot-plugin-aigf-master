"""Bot API 钩子：拦截当前实例插件发出的消息"""

import base64
import ssl
from datetime import datetime

import anyio
import httpx
from nonebot import logger
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Message as OneBotMessage
from nonebot.internal.matcher import current_matcher

from .config import plugin_config
from .context_bus import ContextBus
from .image_handler import ImageHandler
from .models import PluginMessage


def register_hooks(bus: ContextBus, image_handler: ImageHandler,
                   on_plugin_message=None, on_image_start=None, on_image_done=None):
    """注册钩子

    Args:
        on_plugin_message: 回调函数，用于将插件消息加入消息缓冲区
                          签名: (group_id: int, user_name: str, content: str) -> None
    """

    @Bot.on_calling_api
    async def capture_outgoing(bot: Bot, api: str, data: dict):
        """拦截当前实例插件发出的 API 调用"""
        if api not in ("send_msg", "send_group_msg", "send_private_msg"):
            return

        group_id = data.get("group_id")
        if not group_id:
            return

        # 仅处理启用群的插件消息（未启用群不入缓冲、不做 VLM）
        if int(group_id) not in plugin_config.aigfm_enabled_groups:
            return

        # 获取来源插件名（必须有活跃 matcher 才是插件调用）
        try:
            matcher = current_matcher.get()
            source = matcher.plugin_name or "unknown"
        except LookupError:
            # 没有活跃 matcher → 不是插件调用，跳过
            return

        logger.debug(f"[ContextBus] 捕获 outgoing: source={source}, group={group_id}, api={api}")

        # 防死循环：跳过自身
        if source in ("nonebot_plugin_aigf_master", "nonebot-plugin-aigf-master"):
            return

        # 白名单过滤
        if plugin_config.aigfm_capture_plugins and source not in plugin_config.aigfm_capture_plugins:
            return

        # 解析消息内容
        message = data.get("message", "")
        segments = _parse_segments(message)

        for seg in segments:
            if seg["type"] == "text" and seg["text"]:
                bus.push(PluginMessage(
                    content=seg["text"], source_plugin=source,
                    group_id=group_id, timestamp=datetime.now(), message_type="text",
                ))
                if on_plugin_message:
                    on_plugin_message(group_id, source, seg["text"])
            elif seg["type"] == "image" and plugin_config.aigfm_capture_images:
                if on_image_start:
                    on_image_start(group_id)
                if seg.get("url"):
                    await _handle_image_url(bus, image_handler, seg["url"], source, group_id, on_plugin_message, on_image_done)
                elif seg.get("base64"):
                    await _handle_image_base64(bus, image_handler, seg["base64"], source, group_id, on_plugin_message, on_image_done)
                elif seg.get("file"):
                    await _handle_image_file(bus, image_handler, seg["file"], source, group_id, on_plugin_message, on_image_done)


def _parse_segments(message) -> list[dict]:
    """将 OneBot v11 消息统一解析为段落列表"""
    # 字符串消息（可能是 CQ 码），用 OneBotMessage 解析
    if isinstance(message, str):
        try:
            msg = OneBotMessage(message)
            return _parse_segments(msg)
        except Exception:
            return [{"type": "text", "text": message}]

    # Message 对象（必须在 list 之前检查，因为 Message 是 list 的子类）
    if isinstance(message, OneBotMessage):
        result = []
        try:
            for seg in message:
                seg_type = getattr(seg, 'type', None)
                seg_data = getattr(seg, 'data', {})
                if seg_type == "text":
                    text = seg_data.get("text", "") if isinstance(seg_data, dict) else ""
                    if text:
                        result.append({"type": "text", "text": text})
                elif seg_type == "image":
                    data = seg_data if isinstance(seg_data, dict) else {}
                    result.append(_extract_image_data(data))
        except Exception as e:
            logger.error(f"[ContextBus] Message 迭代失败: {e}")
        return result

    # 原始 list 格式（list of dicts）
    if isinstance(message, list):
        result = []
        for seg in message:
            if isinstance(seg, dict):
                if seg.get("type") == "text":
                    result.append({"type": "text", "text": seg.get("data", {}).get("text", "")})
                elif seg.get("type") == "image":
                    result.append(_extract_image_data(seg.get("data", {})))
        return result

    return []


def _extract_image_data(data: dict) -> dict:
    """从图片段落的 data 中提取图片数据，处理 base64:// 前缀"""
    file_value = data.get("file", "")
    url = data.get("url", "")
    b64 = data.get("base64", "")

    # 处理 file=base64://... 格式
    if file_value.startswith("base64://"):
        b64 = file_value[9:]  # 去掉 "base64://" 前缀
        file_value = ""

    return {
        "type": "image",
        "url": url,
        "file": file_value,
        "base64": b64,
    }


async def _process_image_bytes(bus: ContextBus, image_handler: ImageHandler, image_bytes: bytes,
                               source: str, group_id: int, on_plugin_message=None, on_image_done=None):
    """公共图片处理逻辑"""
    try:
        image_base64 = base64.b64encode(image_bytes).decode()
        desc = await image_handler.describe(image_base64, False)
        content = desc.description if desc else "图片"

        bus.push(PluginMessage(
            content=content, source_plugin=source,
            group_id=group_id, timestamp=datetime.now(), message_type="image",
            image_base64=image_base64 if desc else None,
        ))

        if on_plugin_message and content:
            logger.info(f"[ContextBus] 捕获图片: [{source}] {content[:80]}")
            on_plugin_message(group_id, source, f"[图片] {content}", reset_timer=False)
    finally:
        if on_image_done:
            on_image_done(group_id)


async def _handle_image_url(bus: ContextBus, image_handler: ImageHandler, url: str,
                            source: str, group_id: int, on_plugin_message=None, on_image_done=None):
    """处理 URL 格式的图片"""
    try:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        ssl_ctx.set_ciphers("ALL:@SECLEVEL=1")
        async with httpx.AsyncClient(verify=ssl_ctx) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            image_bytes = resp.content
        await _process_image_bytes(bus, image_handler, image_bytes, source, group_id, on_plugin_message, on_image_done)
    except Exception as e:
        logger.error(f"[ContextBus] URL图片处理失败: {e}")


async def _handle_image_base64(bus: ContextBus, image_handler: ImageHandler, b64: str,
                               source: str, group_id: int, on_plugin_message=None, on_image_done=None):
    """处理 base64 格式的图片"""
    try:
        image_bytes = base64.b64decode(b64)
        await _process_image_bytes(bus, image_handler, image_bytes, source, group_id, on_plugin_message, on_image_done)
    except Exception as e:
        logger.error(f"[ContextBus] base64图片处理失败: {e}")


async def _handle_image_file(bus: ContextBus, image_handler: ImageHandler, file_path: str,
                             source: str, group_id: int, on_plugin_message=None, on_image_done=None):
    """处理本地文件格式的图片"""
    try:
        async with await anyio.open_file(file_path, "rb") as f:
            image_bytes = await f.read()
        await _process_image_bytes(bus, image_handler, image_bytes, source, group_id, on_plugin_message, on_image_done)
    except Exception as e:
        logger.error(f"[ContextBus] 本地图片处理失败: {e}")

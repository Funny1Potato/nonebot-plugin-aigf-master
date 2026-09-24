"""Bot API 钩子：拦截当前实例插件发出的消息（跨适配器）

`Bot.on_calling_api` 是 NoneBot 基类钩子（`Bot.call_api` 里触发，所有适配器共用同一套
钩子集合），所以「其它插件发了什么」本身就是跨适配器可获得的。本模块只把原先
onebot 专属的三处实现换成通用实现：

1. api 名过滤 → 改为「`data` 里确实带消息体」才捕获（不再枚举 send_msg 等）
2. 会话来源 → 优先当前事件 + uninfo/alconna 解析，回落扫 `data` 里的会话字段
3. 消息解析 → alconna 的 `UniMessage`（各适配器通用），未映射段按原始段兜底渲染
"""

import base64
import ssl
from datetime import datetime

import anyio
import httpx
from nonebot import logger
from nonebot.adapters import Bot
from nonebot.internal.matcher import current_event, current_matcher
from nonebot_plugin_alconna import UniMessage

from .config import plugin_config
from .context_bus import ContextBus
from .image_handler import ImageHandler, anime_section_text
from .models import PluginMessage
from .processor import SELF_PLUGIN_NAMES
from .session import SessionInfo, adapter_slug, is_enabled, resolve_session, session_key
from .unimsg import RenderDeps, render_incoming

# 消息体可能出现的 api 参数名（不同适配器的发送接口用不同字段名）
_PAYLOAD_KEYS = ("message", "messages", "content", "text", "msg")


def register_hooks(bus: ContextBus, image_handler: ImageHandler,
                   on_plugin_message=None, on_image_start=None, on_image_done=None):
    """注册钩子

    Args:
        on_plugin_message: 回调函数，用于将插件消息加入消息缓冲区
                          签名: (session_key: str, user_name: str, content: str) -> None
    """

    @Bot.on_calling_api
    async def capture_outgoing(bot: Bot, api: str, data: dict):
        """拦截当前实例插件发出的 API 调用"""
        # 来源插件名（必须有活跃 matcher 才是插件调用）
        try:
            matcher = current_matcher.get()
            source = matcher.plugin_name or "unknown"
        except LookupError:
            # 没有活跃 matcher → 不是插件调用，跳过
            return

        # 防死循环：跳过自身
        if source in SELF_PLUGIN_NAMES:
            return

        # 白名单过滤
        if plugin_config.aigfm_capture_plugins and source not in plugin_config.aigfm_capture_plugins:
            return

        # 会话：优先当前事件（响应器上下文里一定有），回落 API 参数
        session = None
        try:
            event = current_event.get()
        except LookupError:
            event = None
        if event is not None:
            session = await resolve_session(bot, event)
        if session is None:
            session = _session_from_api_data(bot, data)
        if session is None:
            return

        # 仅处理启用会话（未启用会话不入缓冲、不做 VLM）
        if not is_enabled(session, plugin_config):
            return

        # 必须有消息体才算「发消息」：避免捕获 get_group_member_info 之类的调用
        payload = _message_payload(bot, data)
        if payload is None:
            return

        logger.debug(f"[ContextBus] 捕获 outgoing: source={source}, session={session.key}, api={api}")

        async def render_image(seg, image_data: dict, is_sticker: bool) -> str:
            # 图片由 VLM 描述后作为独立消息入缓冲（与既有行为一致，不并入文本）
            if plugin_config.aigfm_capture_images:
                await _handle_image(bus, image_handler, image_data, source, session,
                                    on_plugin_message, on_image_start, on_image_done)
            return ""

        deps = RenderDeps(bot_name="", render_image=render_image)
        content, _ = await render_incoming(bot, session, payload, deps)
        if content:
            bus.push(PluginMessage(
                content=content, source_plugin=source,
                session_key=session.key, timestamp=datetime.now(), message_type="text",
            ))
            if on_plugin_message:
                on_plugin_message(session.key, source, content)


def _session_from_api_data(bot: Bot, data: dict) -> SessionInfo | None:
    """没有事件上下文时，从 API 参数里拼出会话（群/频道优先，其次私聊）"""
    slug = adapter_slug(bot.adapter.get_name())
    chat_id = data.get("group_id") or data.get("channel_id") or data.get("chat_id")
    if chat_id:
        guild_id = data.get("guild_id")
        scene_path = f"{guild_id}_{chat_id}" if guild_id else str(chat_id)
        return SessionInfo(
            key=session_key(slug, scene_path), slug=slug, scene_path=scene_path,
            native_chat_id=str(chat_id), adapter_name=bot.adapter.get_name(),
        )
    user_id = data.get("user_id")
    if user_id:
        return SessionInfo(
            key=session_key(slug, str(user_id)), slug=slug, scene_path=str(user_id),
            native_chat_id=str(user_id), adapter_name=bot.adapter.get_name(), is_private=True,
        )
    return None


def _message_payload(bot: Bot, data: dict) -> UniMessage | None:
    """从 API 参数里取出要发送的消息体；取不到返回 None（该调用不是发消息）"""
    message_class = None
    try:
        message_class = bot.adapter.get_message_class()
    except Exception:
        pass
    for key in _PAYLOAD_KEYS:
        value = data.get(key)
        if not value:
            continue
        if message_class is not None and isinstance(value, message_class):
            try:
                return UniMessage.of(value, bot=bot)
            except Exception as e:
                logger.debug(f"[ContextBus] 消息体通用化失败({key}): {e}")
                continue
        if isinstance(value, str):
            # 少数适配器直接以纯文本字段发送
            return UniMessage.text(value)
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
            return UniMessage.text("".join(str(v) for v in value))
    return None


async def _handle_image(bus: ContextBus, image_handler: ImageHandler, data: dict,
                        source: str, session: SessionInfo,
                        on_plugin_message=None, on_image_start=None, on_image_done=None):
    """插件输出的图片：下载 → VLM → 入缓冲（计数由外层 try/finally 成对闭合）"""
    if on_image_start:
        on_image_start(session.key)
    try:
        if data.get("url"):
            await _handle_image_url(bus, image_handler, data["url"], source, session, on_plugin_message)
        elif data.get("base64"):
            await _handle_image_base64(bus, image_handler, data["base64"], source, session, on_plugin_message)
        elif data.get("file"):
            await _handle_image_file(bus, image_handler, data["file"], source, session, on_plugin_message)
    finally:
        # 下载/解码在描述之前就会失败，计数必须在这里闭合，
        # 否则 _pending_images 常驻会让该会话每次批处理都等到上限才放行
        if on_image_done:
            on_image_done(session.key)


async def _process_image_bytes(bus: ContextBus, image_handler: ImageHandler, image_bytes: bytes,
                               source: str, session: SessionInfo, on_plugin_message=None):
    """公共图片处理逻辑"""
    image_base64 = base64.b64encode(image_bytes).decode()
    desc = await image_handler.describe(image_base64, False)
    if desc:
        content = desc.description + anime_section_text(desc)
    else:
        content = "（识图失败）"

    bus.push(PluginMessage(
        content=content, source_plugin=source,
        session_key=session.key, timestamp=datetime.now(), message_type="image",
    ))

    if on_plugin_message and content:
        logger.info(f"[ContextBus] 捕获图片: [{source}] {content[:80]}")
        on_plugin_message(session.key, source, f"[图片] {content}", reset_timer=False)


async def _handle_image_url(bus: ContextBus, image_handler: ImageHandler, url: str,
                            source: str, session: SessionInfo, on_plugin_message=None):
    """处理 URL 格式的图片"""
    try:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        ssl_ctx.set_ciphers("ALL:@SECLEVEL=1")
        async with httpx.AsyncClient(verify=ssl_ctx) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            image_bytes = resp.content
        await _process_image_bytes(bus, image_handler, image_bytes, source, session, on_plugin_message)
    except Exception as e:
        logger.error(f"[ContextBus] URL图片处理失败: {e}")


async def _handle_image_base64(bus: ContextBus, image_handler: ImageHandler, b64: str,
                               source: str, session: SessionInfo, on_plugin_message=None):
    """处理 base64 格式的图片"""
    try:
        image_bytes = base64.b64decode(b64)
        await _process_image_bytes(bus, image_handler, image_bytes, source, session, on_plugin_message)
    except Exception as e:
        logger.error(f"[ContextBus] base64图片处理失败: {e}")


async def _handle_image_file(bus: ContextBus, image_handler: ImageHandler, file_path: str,
                             source: str, session: SessionInfo, on_plugin_message=None):
    """处理本地文件格式的图片"""
    try:
        path = file_path[len("file://"):] if file_path.startswith("file://") else file_path
        if path.startswith("/") and len(path) > 3 and path[2] == ":":
            path = path[1:]          # Windows 的 /D:/... 形式
        async with await anyio.open_file(path, "rb") as f:
            image_bytes = await f.read()
        await _process_image_bytes(bus, image_handler, image_bytes, source, session, on_plugin_message)
    except Exception as e:
        logger.error(f"[ContextBus] 本地图片处理失败: {e}")
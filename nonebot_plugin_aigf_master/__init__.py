"""nonebot-plugin-aigf-master — 群聊 LLM 聊天机器人插件"""

import asyncio
import base64
import json
import re
import ssl
from datetime import datetime

import anyio
import httpx
from nonebot import get_driver, logger, on_command, on_message, require
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import (
    Bot, GroupMessageEvent, MessageSegment, Message as OneBotMessage,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

from .api_hooks import register_hooks, _extract_image_data
from .config import PluginConfig, plugin_config
from .context_bus import ContextBus
from .command_learner import CommandLearner
from .llm_client import LLMClient
from .image_handler import ImageHandler
from .meme_store import MemeStore
from .memory_store import MemoryStore
from .models import ChatMessage
from .peer_client import PeerClient
from .plugin_invoker import PluginInvoker
from .preset_store import PresetStore
from .processor import MessageProcessor
from .search_client import create_search_client

__plugin_meta__ = PluginMetadata(
    name="AI群友（增强版）", description="群聊特化LLM聊天机器人（增强版），具备表情包管理、记忆存储、联网搜索、跨插件感知、插件调用、命令学习等能力。",
    usage="群聊特化LLM聊天机器人", type="application",
    config=PluginConfig, supported_adapters={"~onebot.v11"},
    homepage="https://github.com/Funny1Potato/nonebot-plugin-aigf-master",
    extra={"author": "Funny1Potato"},
)

# ========== 全局实例 ==========

_data_dir = store.get_plugin_data_dir()
_cache_dir = store.get_plugin_cache_dir()
_config_dir = store.get_plugin_config_dir()

_proxy = (plugin_config.aigfm_https_proxy or plugin_config.aigfm_http_proxy) if plugin_config.aigfm_proxy_enabled else None
_llm = LLMClient(plugin_config.aigfm_llm_api_key, plugin_config.aigfm_llm_base_url, _proxy)
_memes = MemeStore(_data_dir, _cache_dir)
_presets = PresetStore(_config_dir)
_bus = ContextBus(plugin_config.aigfm_context_max_messages)
_invoker = PluginInvoker()
_learner = CommandLearner(
    _data_dir,
    min_confidence=plugin_config.aigfm_learn_min_confidence,
)
_image_handler = ImageHandler(_cache_dir)
_search = create_search_client(
    plugin_config.aigfm_search_api, plugin_config.aigfm_search_api_key,
    plugin_config.aigfm_openwebsearch_url,
) if plugin_config.aigfm_search_enabled else None
_peer_client = PeerClient(plugin_config.aigfm_peer_bots) if plugin_config.aigfm_peer_bots else None

_processors: dict[int, MessageProcessor] = {}


def _get_processor(group_id: int) -> MessageProcessor:
    if group_id not in _processors:
        _processors[group_id] = MessageProcessor(
            group_id=str(group_id), llm=_llm,
            memory=MemoryStore(str(group_id), _data_dir),
            memes=_memes, presets=_presets,
            context_bus=_bus, invoker=_invoker, learner=_learner,
            search=_search, config=plugin_config, peer_client=_peer_client,
            peer_scanned_commands=_peer_scanned_commands,
        )
    return _processors[group_id]


# ========== 消息解析 ==========

async def _resolve_user_id(bot: Bot, event: GroupMessageEvent, nickname: str) -> int | None:
    try:
        members = await bot.get_group_member_list(group_id=event.group_id)
        for m in members:
            if m.get("nickname", "").strip() == nickname:
                return m["user_id"]
    except Exception as e:
        logger.error(f"获取群成员列表失败: {e}")
    return None


async def _load_image_bytes(img: dict) -> bytes:
    """按 url → http 形式的 file → base64 → 本地文件 依次取回图片字节

    与 api_hooks 的段落解析保持一致（客户端可能只给 file=xxx 或 base64://...，不一定带 url）
    """
    target = img["url"] or (img["file"] if img["file"].startswith(("http://", "https://")) else "")
    if target:
        cache_path = _cache_dir / "raw"
        cache_path.mkdir(parents=True, exist_ok=True)
        key_match = re.search(r"[?&]fileid=([a-zA-Z0-9_-]+)", target)
        key = key_match.group(1) if key_match else None

        if key and (cache_path / key).exists():
            async with await anyio.open_file(cache_path / key, "rb") as f:
                return await f.read()

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        ssl_ctx.set_ciphers("ALL:@SECLEVEL=1")
        async with httpx.AsyncClient(verify=ssl_ctx) as client:
            resp = await client.get(target)
            resp.raise_for_status()
            data = resp.content
        if key:
            async with await anyio.open_file(cache_path / key, "wb") as f:
                await f.write(data)
        return data

    if img["base64"]:
        return base64.b64decode(img["base64"])
    if img["file"]:
        async with await anyio.open_file(img["file"], "rb") as f:
            return await f.read()
    raise ValueError("图片段缺少 url / file / base64")


async def _parse_message(bot: Bot, event: GroupMessageEvent, message: Message, bot_name: str) -> tuple[str, bool]:
    """解析消息内容，返回 (content, is_at_only)"""
    content = ""
    has_non_at = False

    for seg in message:
        if seg.type == "text":
            text = seg.data.get("text", "")
            content += text
            if text.strip():
                has_non_at = True
        elif seg.type in ("image", "emoji"):
            has_non_at = True
            _on_image_start(event.group_id)
            try:
                img = _extract_image_data(seg.data)
                is_sticker = seg.data.get("sub_type") == 1
                logger.debug(f"[消息解析] 图片: url={img['url'][:60]}, file={img['file'][:60]}, "
                             f"base64={bool(img['base64'])}, is_sticker={is_sticker}")
                image_bytes = await _load_image_bytes(img)
                image_base64 = base64.b64encode(image_bytes).decode()

                if plugin_config.aigfm_image_mode == "llm":
                    cache_id = await _memes.save_to_cache(event.group_id, image_bytes, "", "")
                    content += f"\n[发送了一张图片, id: {cache_id}]\n"
                else:
                    desc = await _image_handler.describe(image_base64, is_sticker)
                    if desc:
                        cache_id = await _memes.save_to_cache(event.group_id, image_bytes, desc.description, desc.emotion)
                        if is_sticker:
                            content += f"\n[发送了一张可能是表情包的图片, id: {cache_id}] [情感:{desc.emotion}] [内容:{desc.description}]\n"
                        else:
                            content += f"\n[发送了一张图片, id: {cache_id}] [内容:{desc.description}]\n"
                    else:
                        # VLM 失败/超时/未启用也要留下痕迹，否则纯图片消息会整条消失
                        content += "\n[发送了一张图片]（识图失败）\n"
            except Exception as e:
                logger.error(f"图片处理错误: {e}")
                content += "\n[图片加载失败]\n"
            finally:
                _on_image_done(event.group_id)
        elif seg.type == "at":
            uid = seg.data.get("qq")
            if not uid:
                continue
            if str(uid) == str(bot.self_id):
                content += f" @{bot_name} "
            else:
                try:
                    info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(uid))
                    content += f" @{info.get('nickname') or uid} "
                except Exception:
                    content += f" @{uid} "
        elif seg.type == "reply":
            has_non_at = True
            try:
                reply_msg_id = seg.data.get("message_id")
                if reply_msg_id:
                    original = await bot.get_msg(message_id=int(reply_msg_id))
                    if original and "message" in original:
                        replied_text = _extract_reply_text(original["message"])
                        replied_sender = original.get("sender", {}).get("nickname", "未知")
                        content += f"[回复 {replied_sender} 的消息: \"{replied_text}\"] "
            except Exception:
                pass
        elif seg.type == "forward":
            has_non_at = True
            content += "[收到一条合并聊天记录] "
        elif seg.type == "json":
            has_non_at = True
            content += _extract_json_desc(seg.data.get("data", ""))
        elif seg.type == "xml":
            has_non_at = True
            content += "[收到一条XML消息] "

    return content.strip(), not has_non_at


def _extract_reply_text(message_content) -> str:
    try:
        if isinstance(message_content, str):
            msg = OneBotMessage(message_content)
        elif isinstance(message_content, list):
            msg = OneBotMessage()
            for seg in message_content:
                if isinstance(seg, dict) and "type" in seg:
                    msg.append(MessageSegment(type=seg["type"], data=seg.get("data", {})))
        else:
            return str(message_content)[:50]
        text = msg.extract_plain_text().strip()
        return text[:50] if text else "（非文字消息）"
    except Exception:
        return "（无法获取）"


def _extract_json_desc(json_str: str) -> str:
    """从 JSON 消息中提取小程序/卡片的 title 和 desc"""
    try:
        data = json.loads(json_str) if isinstance(json_str, str) else json_str
        if isinstance(data, dict):
            # 递归查找 title 和 desc 字段
            title = data.get("title", "")
            desc = data.get("desc", "")
            # 有些小程序在 meta 中
            if not title and "meta" in data:
                meta = data["meta"]
                if isinstance(meta, dict):
                    for v in meta.values():
                        if isinstance(v, dict):
                            title = v.get("title", title) or title
                            desc = v.get("desc", desc) or desc
            if title or desc:
                parts = []
                if title:
                    parts.append(title)
                if desc:
                    parts.append(desc)
                return f"[小程序/卡片: {', '.join(parts)}] "
        return "[收到一条JSON消息] "
    except (json.JSONDecodeError, TypeError):
        return "[收到一条JSON消息] "


# ========== 批量消息处理 ==========

_tasks: set[asyncio.Task] = set()
_group_locks: dict[int, asyncio.Lock] = {}
_group_chunks: dict[int, list[ChatMessage]] = {}
_group_last_time: dict[int, float] = {}
_group_bot: dict[int, Bot] = {}
_group_event: dict[int, GroupMessageEvent] = {}
_pending_images: dict[int, int] = {}
_peer_scanned_commands: dict[str, list[dict]] = {}


def _on_image_start(group_id: int):
    """图片 VLM 开始前：重置批处理计时并标记该群有图片处理中"""
    _group_last_time[group_id] = asyncio.get_event_loop().time()
    _pending_images[group_id] = _pending_images.get(group_id, 0) + 1
    logger.debug(f"[图片] 群{group_id} 图片VLM开始, 已重置计时, 处理中: {_pending_images[group_id]}")


def _on_image_done(group_id: int):
    """图片 VLM 完成/失败：清除图片处理中标记"""
    _pending_images[group_id] = max(0, _pending_images.get(group_id, 0) - 1)
    logger.debug(f"[图片] 群{group_id} 图片VLM完成, 处理中: {_pending_images[group_id]}")


def _add_peer_message(group_id: int, source: str, content: str, reset_timer: bool = True):
    """将其它 bot 推送的消息加入消息缓冲区"""
    msg = ChatMessage(
        time=datetime.now(),
        user_name=source,
        content=content,
        user_id="",
    )
    if group_id not in _group_chunks:
        _group_chunks[group_id] = []
    _group_chunks[group_id].append(msg)
    # 文本消息算新的聊天活动，重置批处理安静计时（图片消息由 VLM 前重置负责，入缓冲不再重置）
    if reset_timer:
        _group_last_time[group_id] = asyncio.get_event_loop().time()


async def _describe_peer_image(group_id: int, source: str, image_url: str = "", image_base64: str = ""):
    """在 Bot A 侧对其它 bot 推送的图片做 VLM 描述后加入消息缓冲区"""
    _on_image_start(group_id)
    try:
        if image_url:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
            ssl_ctx.set_ciphers("ALL:@SECLEVEL=1")
            async with httpx.AsyncClient(verify=ssl_ctx) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                image_bytes = resp.content
        elif image_base64:
            image_bytes = base64.b64decode(image_base64)
        else:
            return
        image_b64 = base64.b64encode(image_bytes).decode()
        desc = await _image_handler.describe(image_b64, False)
        content = f"[图片] {desc.description if desc else '（识图失败）'}"
        _add_peer_message(group_id, source, content, reset_timer=False)
        logger.info(f"[Peer] 捕获图片: [{source}] {content[:80]}")
    except Exception as e:
        logger.error(f"[Peer] 图片描述失败: {e}")
    finally:
        _on_image_done(group_id)


async def _handle_peer_capture(group_id: int, source: str, data: dict):
    """后台处理其它 bot 推送的消息（文本直接入缓冲；图片按 aigfm_capture_images 决定是否走 VLM）"""
    if data.get("commands"):
        # 更新该 bot 的已注册命令列表（供 LLM 了解可调用命令）
        _peer_scanned_commands[source] = data["commands"]
        logger.debug(f"[Peer] 更新命令列表: bot={source}, {len(data['commands'])} 个")
    # 未启用群：不入缓冲、不做 VLM
    if group_id not in plugin_config.aigfm_enabled_groups:
        return
    if data.get("text"):
        _add_peer_message(group_id, source, data["text"])
    # 与本机插件捕获同开关：关闭时不下载、不做 VLM、也不入缓冲
    if (data.get("image_url") or data.get("image_base64")) and plugin_config.aigfm_capture_images:
        await _describe_peer_image(group_id, source, data.get("image_url", ""), data.get("image_base64", ""))


def _get_lock(group_id: int) -> asyncio.Lock:
    if group_id not in _group_locks:
        _group_locks[group_id] = asyncio.Lock()
    return _group_locks[group_id]


async def _batch_processor(group_id: int):
    """每群的批量消息处理循环"""
    while True:
        await asyncio.sleep(1.0)
        lock = _get_lock(group_id)
        async with lock:
            chunk = _group_chunks.get(group_id, [])
            if not chunk:
                continue
            bot = _group_bot.get(group_id)
            event = _group_event.get(group_id)
            if not bot or not event:
                # 本进程内该群还没有真实用户消息（只有插件/peer 推送）：没有可回复的事件，
                # 保留缓冲等首条用户消息到达后一起处理，只截断长度防止无限增长
                if len(chunk) > plugin_config.aigfm_context_max_messages:
                    del chunk[: len(chunk) - plugin_config.aigfm_context_max_messages]
                continue
            now = asyncio.get_event_loop().time()
            last_time = _group_last_time.get(group_id, 0)

            # 有图片正在下载/VLM 解析时整批推迟触发，等内容填好再发，避免把无描述的占位消息给 LLM；
            # 超过绝对上限仍挂着（解析卡死或计数泄漏）则放行，保证该群不会永久沉默
            pending = _pending_images.get(group_id, 0)
            if pending > 0:
                if (now - last_time) < (plugin_config.aigfm_batch_timeout + plugin_config.aigfm_incomplete_timeout):
                    logger.debug(f"[图片] 群{group_id} {pending} 张解析中，推迟触发")
                    continue
                logger.warning(f"[图片] 群{group_id} 解析超过上限，按占位内容触发")

            # 判断是否触发
            reached_count = len(chunk) >= plugin_config.aigfm_batch_count
            last_msg = chunk[-1]
            effective_timeout = plugin_config.aigfm_batch_timeout
            if _pending_images.get(group_id, 0) > 0:
                # 有图片正在 VLM 处理，用更长的等待时长
                effective_timeout = plugin_config.aigfm_incomplete_timeout
            elif last_msg.is_at_only:
                effective_timeout = plugin_config.aigfm_incomplete_timeout
            elif len(chunk) >= 2:
                prev = chunk[-2]
                if last_msg.user_id == prev.user_id and (last_msg.time - prev.time).total_seconds() < plugin_config.aigfm_merge_window:
                    effective_timeout = plugin_config.aigfm_incomplete_timeout
            reached_time = (now - last_time) >= effective_timeout

            if not reached_count and not reached_time:
                continue

            messages = chunk.copy()
            _group_chunks[group_id].clear()

        # 处理
        processor = _get_processor(group_id)
        processor._bot = bot
        stickers = _memes.get_cached(group_id)

        try:
            responses = await processor.process(messages, cached_stickers=stickers)
            if stickers:
                _memes.clear_cache(group_id)
        except Exception as e:
            logger.error(f"[处理失败] 群{group_id}: {e}")
            import traceback
            traceback.print_exc()
            continue

        if not responses:
            continue

        # 发送回复
        try:
            pending = OneBotMessage()
            for resp in responses:
                if resp.type == "text":
                    pending.append(MessageSegment.text(resp.content))
                elif resp.type == "at":
                    uid = await _resolve_user_id(bot, event, resp.user_name)
                    if uid:
                        pending.append(MessageSegment.at(uid))
                elif resp.type == "meme":
                    if pending:
                        await bot.send(message=pending, event=event)
                        pending = OneBotMessage()
                    path = _memes.resolve(resp.meme_id)
                    if path:
                        await bot.send(message=MessageSegment.image(path), event=event)
                        await _memes.persist()
            if pending:
                await bot.send(message=pending, event=event)
                logger.success("[发送] 消息已发送")
        except Exception as e:
            logger.error(f"发送消息失败: {e}")


# ========== 命令注册 ==========

def _is_group_msg(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent)


# 这里新增/改名的管理命令必须同步 processor.SELF_COMMANDS：
# synthetic 事件携带真实触发用户的 user_id，少了同步会让 LLM 有机会代为执行这些命令
status_cmd = on_command(rule=_is_group_msg, permission=SUPERUSER,
                        cmd="status", aliases={"状态"}, priority=0, block=True)
set_role_cmd = on_command(rule=_is_group_msg, permission=SUPERUSER,
                          cmd="set_role", aliases={"设置角色"}, priority=0, block=True)
reset_cmd = on_command(rule=_is_group_msg, permission=SUPERUSER,
                       cmd="reset", aliases={"重置"}, priority=0, block=True)
presets_cmd = on_command(rule=_is_group_msg, permission=SUPERUSER,
                         cmd="presets", aliases={"preset"}, priority=0, block=True)
set_preset_cmd = on_command(rule=_is_group_msg, permission=SUPERUSER,
                            cmd="set_preset", aliases={"set_presets"}, priority=0, block=True)
reload_meme_cmd = on_command(rule=_is_group_msg, permission=SUPERUSER,
                             cmd="reload_meme", aliases={"重载表情包"}, priority=0, block=True)
auto_chat = on_message(rule=_is_group_msg, priority=1, block=False)


@status_cmd.handle()
async def _(event: GroupMessageEvent):
    processor = _get_processor(event.group_id)
    await status_cmd.finish(processor.status())


@set_role_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    parts = args.extract_plain_text().strip().split(" ", 1)
    if len(parts) != 2:
        await set_role_cmd.finish("用法: set_role <名字> <设定>")
    processor = _get_processor(event.group_id)
    processor.bot_name, processor.bot_role = parts[0], parts[1]
    await set_role_cmd.finish(f"角色已设为: {parts[0]}\n设定: {parts[1]}")


@reset_cmd.handle()
async def _(event: GroupMessageEvent):
    processor = _get_processor(event.group_id)
    processor.bot_name = "小助手"
    processor.bot_role = "一个友好的群聊助手"
    processor.recent_messages.clear()
    # 插件响应另有一份存在 ContextBus 里，不清会跨 reset 残留进后续 prompt
    processor.context_bus.clear(int(event.group_id))
    processor.social_energy = 0.75
    await processor.load_preset(plugin_config.aigfm_default_preset)
    await reset_cmd.finish("已重置会话")


@presets_cmd.handle()
async def _():
    all_presets = _presets.list_all()
    msg = "可用预设:\n" + "\n".join(f"- {k}: {v.name} {v.role}" for k, v in all_presets.items() if not v.hidden)
    msg += "\n用法: set_preset <预设名>"
    await presets_cmd.finish(msg)


@set_preset_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    name = args.extract_plain_text().strip()
    if not name:
        await set_preset_cmd.finish("用法: set_preset <预设名>")
    processor = _get_processor(event.group_id)
    if await processor.load_preset(name):
        await set_preset_cmd.finish(f"预设已加载: {name}")
    else:
        await set_preset_cmd.finish(f"不存在的预设: {name}")


@reload_meme_cmd.handle()
async def _():
    await _memes.load_all()
    await reload_meme_cmd.finish(f"已重载。管理员: {len(_memes._admin_memes)} 个, 自动收集: {len(_memes._collected_memes)} 个")


@auto_chat.handle()
async def handle_auto_chat(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    if group_id not in plugin_config.aigfm_enabled_groups:
        return
    if event.get_user_id() == str(bot.self_id):
        return
    # 只跳过 invoker 自己合成的事件（否则自己发起的命令会被当成用户重复发送）
    if _invoker and _invoker.is_synthetic(event):
        return
    logger.success(f"[接收] 群{group_id} 收到消息 from {event.user_id}")

    processor = _get_processor(group_id)

    # 先加入 chunk（占位），并设置计时器/bot/event。
    # 防止解析期间插件响应先入 chunk，批处理用 last_time=0 立即触发，导致用户消息被拆到下一批
    # 占位带上即时可得的内容（只拼文本段，不调任何 API），含图片则标注解析中：
    # 这样即便批处理在解析期间触发，LLM 也不会收到空消息
    segs = event.original_message
    instant = "".join(s.data.get("text", "") for s in segs if s.type == "text")
    if any(s.type in ("image", "emoji") for s in segs):
        instant += "\n[图片解析中]\n"

    msg = ChatMessage(
        time=datetime.now(),
        user_name=str(event.get_user_id()),
        user_id=event.get_user_id(),
        content=instant,
        is_at_only=False,
    )
    async with _get_lock(group_id):
        if group_id not in _group_chunks:
            _group_chunks[group_id] = []
        _group_chunks[group_id].append(msg)
        _group_last_time[group_id] = asyncio.get_event_loop().time()
        _group_bot[group_id] = bot
        _group_event[group_id] = event
        # 解析可能挂几十秒（VLM），身份要先就位，否则期间触发的插件调用会用上一个人的 QQ
        processor._current_user_id = int(event.get_user_id())

    # 锁外异步解析消息内容（含图片下载/VLM 等慢操作）
    content, is_at_only = await _parse_message(bot, event, event.original_message, processor.bot_name)

    if not content:
        # 无有效内容，移除占位消息
        async with _get_lock(group_id):
            chunk = _group_chunks.get(group_id)
            if chunk and msg in chunk:
                chunk.remove(msg)
        return

    # 获取用户信息
    user_info: dict = {}
    try:
        user_info = await bot.get_group_member_info(group_id=group_id, user_id=int(event.get_user_id()))
        nickname = user_info.get("nickname") or event.get_user_id()
    except Exception:
        nickname = event.get_user_id()

    # 更新占位消息内容
    msg.user_name = nickname
    msg.content = content
    msg.is_at_only = is_at_only

    # 自动更新昵称
    try:
        if user_info.get("nickname"):
            await processor.memory.update_nickname(event.get_user_id(), user_info["nickname"])
    except Exception:
        pass


# ========== 启动 ==========

@get_driver().on_startup
async def _on_startup():
    logger.success(f"[启动] 图片理解模式: {plugin_config.aigfm_image_mode}")
    if plugin_config.aigfm_search_enabled:
        logger.success(f"[启动] 联网搜索: 已启用 | API: {plugin_config.aigfm_search_api}")
    else:
        logger.info("[启动] 联网搜索: 未启用")

    await _presets.load_all()
    await _memes.load_all()
    await _memes.cleanup()

    # 加载命令学习器
    if plugin_config.aigfm_learn_commands:
        await _learner.load()
        logger.success(f"[启动] 命令学习: 已启用 | 最小置信度: {plugin_config.aigfm_learn_min_confidence}")

    # 注册钩子
    def _on_plugin_message(group_id: int, source: str, content: str, reset_timer: bool = True):
        """将插件/bot消息添加到消息缓冲区"""
        msg = ChatMessage(
            time=datetime.now(),
            user_name=source,
            content=content,
            user_id="",
        )
        if group_id not in _group_chunks:
            _group_chunks[group_id] = []
        _group_chunks[group_id].append(msg)
        # 文本消息算新的聊天活动，重置批处理安静计时（图片消息由 VLM 前重置负责，入缓冲不再重置）
        if reset_timer:
            _group_last_time[group_id] = asyncio.get_event_loop().time()

    register_hooks(_bus, _image_handler, _on_plugin_message, _on_image_start, _on_image_done)

    # 注册跨 bot 接收端点（其它 bot 推送消息到此）
    if _peer_client:
        try:
            import nonebot
            from fastapi import Request
            from fastapi.responses import JSONResponse
            app = nonebot.get_app()

            @app.post("/peer/capture")
            async def _peer_capture(request: Request):
                auth = request.headers.get("Authorization", "")
                token = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
                peer_name = None
                for cfg in plugin_config.aigfm_peer_bots:
                    if cfg.get("token") and cfg.get("token") == token:
                        peer_name = cfg.get("name")
                        break
                if not peer_name:
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                try:
                    data = await request.json()
                except Exception:
                    return JSONResponse({"error": "bad json"}, status_code=400)
                group_id = data.get("group_id")
                if not group_id:
                    return JSONResponse({"error": "no group_id"}, status_code=400)
                source = peer_name
                logger.info(f"[Peer] 收到推送: bot={source}, group={group_id}, "
                            f"text={str(data.get('text', ''))[:80]}, "
                            f"image_url={bool(data.get('image_url'))}, image_base64={bool(data.get('image_base64'))}")
                # 文本/图片统一后台处理，立即返回，避免推送方超时
                _peer_task = asyncio.create_task(_handle_peer_capture(int(group_id), source, data))
                _tasks.add(_peer_task)
                _peer_task.add_done_callback(_tasks.discard)
                return JSONResponse({"ok": True})

            logger.success(f"[启动] 跨 bot 通信: 已启用 | bots: {len(plugin_config.aigfm_peer_bots)} 个")
        except Exception as e:
            logger.error(f"[Peer] 注册接收端点失败: {e}")

    # 初始化每群处理器并加载默认预设
    for gid in plugin_config.aigfm_enabled_groups:
        processor = _get_processor(gid)
        await processor.load_preset(plugin_config.aigfm_default_preset)
        # 启动批处理任务
        task = asyncio.create_task(_batch_processor(gid))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

"""nonebot-plugin-aigf-master — 群聊 LLM 聊天机器人插件（跨适配器）

收发与会话身份都走通用层：消息用 nonebot_plugin_alconna 的 uniseg，会话/用户信息用
nonebot_plugin_uninfo，因此只要适配器被这两个库支持就能工作。会话以 `适配器:会话id`
为键（旧版裸 id 的目录在启动时迁移为 onebot11_ 前缀）。
"""

import asyncio
import base64
import re
import ssl
from datetime import datetime

import anyio
import httpx
from nonebot import get_driver, logger, on_message, on_notice, require
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

# require 必须先于任何 import（否则库会以「先被 import」的方式装载，require 会判定它不是插件）
require("nonebot_plugin_localstore")
require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")
import nonebot_plugin_localstore as store
from nonebot_plugin_alconna import (
    Alconna, Args, CommandResult, Image, MultiVar, Text, UniMessage, on_alconna,
)

from .api_hooks import register_hooks
from .config import PluginConfig, plugin_config
from .context_bus import ContextBus
from .command_learner import CommandLearner
from .llm_client import LLMClient
from .anime_recognizer import effective_backend
from .cache_cleanup import prune_dir
from .image_handler import ImageHandler, anime_section_text
from .image_gen_client import ImageGenClient
from .meme_store import MemeStore
from .memory_store import MemoryStore
from .models import ChatMessage
from .notice import is_group_notice, notice_text
from .peer_client import PeerClient
from .plugin_invoker import PluginInvoker
from .preset_store import PresetStore
from .processor import MessageProcessor
from .search_client import create_search_client
from .session import (
    SessionInfo, adapter_slug, disk_key, is_enabled, migrate_legacy_dirs,
    normalize_enabled, resolve_session, session_key,
)
from .unimsg import RenderDeps, build_unimsg, compose_unimsg_list, image_data, render_incoming, send_unimsg

__plugin_meta__ = PluginMetadata(
    name="AI群友（增强版）", description="群聊特化LLM聊天机器人（增强版），具备表情包管理、记忆存储、联网搜索、跨插件感知、插件调用、命令学习等能力。",
    usage="群聊特化LLM聊天机器人", type="application",
    config=PluginConfig,
    # 收发走 alconna 的 uniseg、会话/成员信息走 uninfo，因此只声明两者共同支持的适配器
    # （两者都不支持的适配器上本插件无法工作；inherit_supported_adapters 取交集，需在上面的 require 之后调用）
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna",
        "nonebot_plugin_uninfo",
    ),
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
_image_gen = ImageGenClient(
    plugin_config.aigfm_image_gen_api_key or plugin_config.aigfm_llm_api_key,
    plugin_config.aigfm_image_gen_model,
    plugin_config.aigfm_image_gen_base_url,
    _proxy,
) if plugin_config.aigfm_image_gen_enabled else None
_search = create_search_client(
    plugin_config.aigfm_search_api, plugin_config.aigfm_search_api_key,
    plugin_config.aigfm_openwebsearch_url,
) if plugin_config.aigfm_search_enabled else None
_peer_client = PeerClient(plugin_config.aigfm_peer_bots) if plugin_config.aigfm_peer_bots else None

_processors: dict[str, MessageProcessor] = {}


def _get_processor(key: str) -> MessageProcessor:
    if key not in _processors:
        _processors[key] = MessageProcessor(
            session_key=key, llm=_llm,
            memory=MemoryStore(disk_key(key), _data_dir),
            memes=_memes, presets=_presets,
            context_bus=_bus, invoker=_invoker, learner=_learner,
            search=_search, config=plugin_config, peer_client=_peer_client,
            peer_scanned_commands=_peer_scanned_commands,
            image_gen=_image_gen, image_handler=_image_handler,
        )
    return _processors[key]


def _enabled_keys() -> set[str]:
    """启用会话键集合（群/频道 + 私聊）"""
    return normalize_enabled(plugin_config.aigfm_enabled_groups) | \
        normalize_enabled(plugin_config.aigfm_enabled_private)


def _maybe_enabled(bot: Bot, event: Event) -> bool:
    """不发 API 请求的启用预筛（避免为未启用会话白白调 uninfo）"""
    try:
        from nonebot_plugin_alconna import get_target
        target = get_target(event, bot)
    except Exception:
        return True          # 解析不出就放行，交给 resolve_session 判定
    path = f"{target.parent_id}_{target.id}" if (target.channel and target.parent_id) else target.id
    return session_key(adapter_slug(bot.adapter.get_name()), str(path)) in _enabled_keys()


def _user_id(event: Event) -> str:
    try:
        return str(event.get_user_id())
    except Exception:
        return ""


# ========== 消息解析（通用） ==========

async def _load_image_bytes(img: dict) -> bytes:
    """按 url → http 形式的 file → base64 → 本地文件 依次取回图片字节

    与图片段的解析保持一致（客户端可能只给 file=xxx 或 base64://...，不一定带 url）
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
            prune_dir(cache_path, plugin_config.aigfm_raw_cache_max_files)
        return data

    if img["base64"]:
        return base64.b64decode(img["base64"])
    if img["file"]:
        async with await anyio.open_file(img["file"], "rb") as f:
            return await f.read()
    raise ValueError("图片段缺少 url / file / base64")


async def _render_image_text(session_key_: str, image: dict, is_sticker: bool) -> str:
    """图片段 → prompt 文本（下载 → 缓存 → VLM/角色识别）"""
    _on_image_start(session_key_)
    try:
        image_bytes = await _load_image_bytes(image)
        image_base64 = base64.b64encode(image_bytes).decode()

        if plugin_config.aigfm_image_mode == "llm":
            cache_id = await _memes.save_to_cache(session_key_, image_bytes, "", "")
            return f"\n[发送了一张图片, id: {cache_id}]\n"

        desc = await _image_handler.describe(image_base64, is_sticker)
        if not desc:
            # VLM 失败/超时/未启用也要留下痕迹，否则纯图片消息会整条消失
            return "\n[发送了一张图片]（识图失败）\n"
        cache_id = await _memes.save_to_cache(session_key_, image_bytes, desc.description, desc.emotion)
        anime_text = anime_section_text(desc)
        if is_sticker:
            return (f"\n[发送了一张可能是表情包的图片, id: {cache_id}] "
                    f"[情感:{desc.emotion}] [内容:{desc.description}]{anime_text}\n")
        return f"\n[发送了一张图片, id: {cache_id}] [内容:{desc.description}]{anime_text}\n"
    except Exception as e:
        logger.error(f"图片处理错误: {e}")
        return "\n[图片加载失败]\n"
    finally:
        _on_image_done(session_key_)


def _render_deps(session_key_: str, bot_name: str) -> RenderDeps:
    async def render_image(seg, data: dict, is_sticker: bool) -> str:
        return await _render_image_text(session_key_, data, is_sticker)
    return RenderDeps(bot_name=bot_name, render_image=render_image)


async def _parse_message(bot: Bot, session: SessionInfo, unimsg: UniMessage,
                         bot_name: str) -> tuple[str, bool]:
    """解析消息内容，返回 (content, is_at_only)"""
    deps = _render_deps(session.key, bot_name)
    return await render_incoming(bot, session, unimsg, deps)


# ========== 批量消息处理 ==========

_tasks: set[asyncio.Task] = set()
_group_locks: dict[str, asyncio.Lock] = {}
_group_chunks: dict[str, list[ChatMessage]] = {}
_group_last_time: dict[str, float] = {}
_group_bot: dict[str, Bot] = {}
_group_event: dict[str, Event] = {}
_pending_images: dict[str, int] = {}
_peer_scanned_commands: dict[str, list[dict]] = {}


def _on_image_start(session_key_: str):
    """图片 VLM 开始前：重置批处理计时并标记该会话有图片处理中"""
    _group_last_time[session_key_] = asyncio.get_event_loop().time()
    _pending_images[session_key_] = _pending_images.get(session_key_, 0) + 1
    logger.debug(f"[图片] {session_key_} 图片VLM开始, 已重置计时, 处理中: {_pending_images[session_key_]}")


def _on_image_done(session_key_: str):
    """图片 VLM 完成/失败：清除图片处理中标记"""
    _pending_images[session_key_] = max(0, _pending_images.get(session_key_, 0) - 1)
    logger.debug(f"[图片] {session_key_} 图片VLM完成, 处理中: {_pending_images[session_key_]}")


def _add_external_message(session_key_: str, source: str, content: str, reset_timer: bool = True):
    """将插件/其它 bot 推送的消息加入消息缓冲区"""
    msg = ChatMessage(
        time=datetime.now(),
        user_name=source,
        content=content,
        user_id="",
    )
    if session_key_ not in _group_chunks:
        _group_chunks[session_key_] = []
    _group_chunks[session_key_].append(msg)
    # 文本消息算新的聊天活动，重置批处理安静计时（图片消息由 VLM 前重置负责，入缓冲不再重置）
    if reset_timer:
        _group_last_time[session_key_] = asyncio.get_event_loop().time()


def _add_notice_message(session_key_: str, content: str):
    """将系统通知（戳一戳/禁言/进出群/消息撤回）加入消息缓冲区

    user_id 留空 → 渲染为 `[系统]: '内容'`，LLM 可区分系统通知与群友发言
    """
    if session_key_ not in _group_chunks:
        _group_chunks[session_key_] = []
    _group_chunks[session_key_].append(ChatMessage(
        time=datetime.now(),
        user_name="系统",
        content=content,
        user_id="",
    ))
    _group_last_time[session_key_] = asyncio.get_event_loop().time()


async def _describe_peer_image(session_key_: str, source: str, image_url: str = "", image_base64: str = ""):
    """在本侧对其它 bot 推送的图片做 VLM 描述后加入消息缓冲区"""
    _on_image_start(session_key_)
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
        if desc:
            content = f"[图片] {desc.description}{anime_section_text(desc)}"
        else:
            content = "[图片] （识图失败）"
        _add_external_message(session_key_, source, content, reset_timer=False)
        logger.info(f"[Peer] 捕获图片: [{source}] {content[:80]}")
    except Exception as e:
        logger.error(f"[Peer] 图片描述失败: {e}")
    finally:
        _on_image_done(session_key_)


async def _handle_peer_capture(group_id, source: str, data: dict):
    """后台处理其它 bot 推送的消息（文本直接入缓冲；图片按 aigfm_capture_images 决定是否走 VLM）"""
    if data.get("commands"):
        # 更新该 bot 的已注册命令列表（供 LLM 了解可调用命令）
        _peer_scanned_commands[source] = data["commands"]
        logger.debug(f"[Peer] 更新命令列表: bot={source}, {len(data['commands'])} 个")
    # peer 0.4.0+ 直接给会话键（支持多适配器）；旧 peer 只有数字 group_id（按 onebot11 映射）
    key = data.get("session") or session_key("onebot11", str(group_id))
    # 未启用会话：不入缓冲、不做 VLM
    if key not in _enabled_keys():
        return
    if data.get("text"):
        _add_external_message(key, source, data["text"])
    # 与本机插件捕获同开关：关闭时不下载、不做 VLM、也不入缓冲
    if (data.get("image_url") or data.get("image_base64")) and plugin_config.aigfm_capture_images:
        await _describe_peer_image(key, source, data.get("image_url", ""), data.get("image_base64", ""))


def _get_lock(session_key_: str) -> asyncio.Lock:
    if session_key_ not in _group_locks:
        _group_locks[session_key_] = asyncio.Lock()
    return _group_locks[session_key_]


async def _batch_processor(session_key_: str):
    """每会话的批量消息处理循环"""
    while True:
        await asyncio.sleep(1.0)
        lock = _get_lock(session_key_)
        async with lock:
            chunk = _group_chunks.get(session_key_, [])
            if not chunk:
                continue
            bot = _group_bot.get(session_key_)
            event = _group_event.get(session_key_)
            if not bot or not event:
                # 本进程内该会话还没有真实用户消息（只有插件/peer 推送）：没有可回复的事件，
                # 保留缓冲等首条用户消息到达后一起处理，只截断长度防止无限增长
                if len(chunk) > plugin_config.aigfm_context_max_messages:
                    del chunk[: len(chunk) - plugin_config.aigfm_context_max_messages]
                continue
            now = asyncio.get_event_loop().time()
            last_time = _group_last_time.get(session_key_, 0)

            # 有图片正在下载/VLM 解析时整批推迟触发，等内容填好再发，避免把无描述的占位消息给 LLM；
            # 超过绝对上限仍挂着（解析卡死或计数泄漏）则放行，保证该会话不会永久沉默
            pending = _pending_images.get(session_key_, 0)
            if pending > 0:
                if (now - last_time) < (plugin_config.aigfm_batch_timeout + plugin_config.aigfm_incomplete_timeout):
                    logger.debug(f"[图片] {session_key_} {pending} 张解析中，推迟触发")
                    continue
                logger.warning(f"[图片] {session_key_} 解析超过上限，按占位内容触发")

            # 判断是否触发
            reached_count = len(chunk) >= plugin_config.aigfm_batch_count
            last_msg = chunk[-1]
            effective_timeout = plugin_config.aigfm_batch_timeout
            if last_msg.is_at_only:
                effective_timeout = plugin_config.aigfm_incomplete_timeout
            elif len(chunk) >= 2:
                prev = chunk[-2]
                if last_msg.user_id == prev.user_id and (last_msg.time - prev.time).total_seconds() < plugin_config.aigfm_merge_window:
                    effective_timeout = plugin_config.aigfm_incomplete_timeout
            reached_time = (now - last_time) >= effective_timeout

            if not reached_count and not reached_time:
                continue

            messages = chunk.copy()
            _group_chunks[session_key_].clear()

        # 处理
        processor = _get_processor(session_key_)
        processor._bot = bot
        # 缓存索引常驻（不随批处理清空），历史图片的 cache_id 才能跨批收藏/作生图参考；
        # 条目数由 save_to_cache 的每会话上限与 sticker_cache 文件上限共同兜底
        stickers = _memes.get_cached(session_key_)

        try:
            responses = await processor.process(messages, cached_stickers=stickers)
        except Exception as e:
            logger.error(f"[处理失败] {session_key_}: {e}")
            import traceback
            traceback.print_exc()
            continue

        if not responses:
            continue

        # 发送回复（段落拼装与插件调用共用 compose_unimsg_list）
        try:
            parts = []
            for resp in responses:
                if resp.type == "text":
                    parts.append({"type": "text", "content": resp.content})
                elif resp.type == "at":
                    parts.append({"type": "at", "target": resp.user_name})
                elif resp.type == "meme":
                    path = _memes.resolve(resp.meme_id)
                    if path:
                        parts.append({"type": "image", "path": path})
            if not parts:
                continue
            target = getattr(processor._session, "target", None) or event
            messages, _ = await compose_unimsg_list(bot, processor._session, parts)
            for msg in messages:
                await send_unimsg(bot, target, msg)
            # 与旧实现一致的日志语义：仅当结尾还有文本消息要发时才记成功
            if messages and messages[-1] and isinstance(messages[-1][0], Text):
                logger.success("[发送] 消息已发送")
            if any(p["type"] == "image" for p in parts):
                await _memes.persist()
        except Exception as e:
            logger.error(f"发送消息失败: {e}")


# ========== 命令注册 ==========

def _is_message(event: Event) -> bool:
    """通用消息事件判定（各适配器各自定义 MessageEvent，NoneBot 没有统一基类）

    能取到消息对象、且事件类型是 message 即认为是消息事件；会话是否启用由处理函数判定。
    """
    if not callable(getattr(event, "get_message", None)):
        return False
    try:
        return event.get_type() == "message"
    except Exception:
        return True


async def _command_session(bot: Bot, event: Event) -> SessionInfo | None:
    """管理命令用：解析当前会话（解析不出时给用户一句可读的提示）"""
    return await resolve_session(bot, event)


# 这里新增/改名的管理命令必须同步 processor.SELF_COMMANDS：
# synthetic 事件携带真实触发用户的 user_id，少了同步会让 LLM 有机会代为执行这些命令
#
# 末尾的 MultiVar(str, "*") 只用来容忍命令后的多余文本（与旧的 on_command 行为一致），
# 命令头仍需位于消息开头，因此句中提及不会误触发。
#
# 必须持有 Alconna 对象的强引用：AlconnaRule 只持弱引用（rule.py 里 self.command = weakref.ref(...)），
# command_manager 也用 WeakValueDictionary 存命令，写 `on_alconna(Alconna(...))` 会让命令被 GC 回收，
# 此后该 matcher 的规则静默返回 False（命令“随机失效”，排查极难）。
_ALCONNA_REFS: list[Alconna] = []


def _cmd(*args, **kwargs) -> Alconna:
    """构造并长期持有管理命令的 Alconna（见上面的弱引用说明）"""
    alc = Alconna(*args, **kwargs)
    _ALCONNA_REFS.append(alc)
    return alc


status_cmd = on_alconna(_cmd("status", Args["rest", MultiVar(str, "*")]), aliases={"状态"},
                        permission=SUPERUSER, priority=0, block=True)
set_role_cmd = on_alconna(_cmd("set_role", Args["rest", MultiVar(str, "*")]),
                          aliases={"设置角色"}, permission=SUPERUSER, priority=0, block=True)
reset_cmd = on_alconna(_cmd("reset", Args["rest", MultiVar(str, "*")]), aliases={"重置"},
                       permission=SUPERUSER, priority=0, block=True)
presets_cmd = on_alconna(_cmd("presets", Args["rest", MultiVar(str, "*")]), aliases={"preset"},
                         permission=SUPERUSER, priority=0, block=True)
set_preset_cmd = on_alconna(_cmd("set_preset", Args["rest", MultiVar(str, "*")]),
                            aliases={"set_presets"}, permission=SUPERUSER, priority=0, block=True)
reload_meme_cmd = on_alconna(_cmd("reload_meme", Args["rest", MultiVar(str, "*")]),
                             aliases={"重载表情包"}, permission=SUPERUSER, priority=0, block=True)
auto_chat = on_message(rule=_is_message, priority=1, block=False)
# 类型闸门是内核给的（Matcher.check_rule 按 event.get_type() == "notice" 过滤，见 notice.py 开头），
# 所以这里收的是所有适配器的通知事件；rule 只做「带会话线索」的廉价预筛
group_notice = on_notice(rule=is_group_notice, priority=1, block=False)


@status_cmd.handle()
async def _(bot: Bot, event: Event):
    session = await _command_session(bot, event)
    if session is None:
        await status_cmd.finish("无法识别当前会话（该适配器缺少会话信息）")
    processor = _get_processor(session.key)
    await status_cmd.finish(processor.status())


@set_role_cmd.handle()
async def _(bot: Bot, event: Event, result: CommandResult):
    # 与旧实现一致：命令后的第一个空格前是名字，其余整体是设定
    parts = [str(item) for item in (result.result.all_matched_args.get("rest") or ())]
    name = parts[0].strip() if parts else ""
    role = " ".join(parts[1:]).strip()
    if not name or not role:
        await set_role_cmd.finish("用法: set_role <名字> <设定>")
    session = await _command_session(bot, event)
    if session is None:
        await set_role_cmd.finish("无法识别当前会话（该适配器缺少会话信息）")
    processor = _get_processor(session.key)
    processor.bot_name, processor.bot_role = name, role
    await set_role_cmd.finish(f"角色已设为: {name}\n设定: {role}")


@reset_cmd.handle()
async def _(bot: Bot, event: Event):
    session = await _command_session(bot, event)
    if session is None:
        await reset_cmd.finish("无法识别当前会话（该适配器缺少会话信息）")
    processor = _get_processor(session.key)
    processor.bot_name = "小助手"
    processor.bot_role = "一个友好的群聊助手"
    processor.recent_messages.clear()
    # 落盘的聊天记录一起清，否则重置后重启又会把旧历史读回来
    await processor.memory.clear_recent()
    # 插件响应另有一份存在 ContextBus 里，不清会跨 reset 残留进后续 prompt
    processor.context_bus.clear(session.key)
    # 图片缓存索引随聊天记录一起清（历史 id 已不在 prompt 中，留着也无可收藏的上下文）
    _memes.clear_cache(session.key)
    # 调用台账一起清：重置后不应再被「刚调用过同一条命令」挡住
    processor._invoke_log.clear()
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
async def _(bot: Bot, event: Event, result: CommandResult):
    preset_name = " ".join(str(item) for item in (result.result.all_matched_args.get("rest") or ())).strip()
    if not preset_name:
        await set_preset_cmd.finish("用法: set_preset <预设名>")
    session = await _command_session(bot, event)
    if session is None:
        await set_preset_cmd.finish("无法识别当前会话（该适配器缺少会话信息）")
    processor = _get_processor(session.key)
    if await processor.load_preset(preset_name):
        await set_preset_cmd.finish(f"预设已加载: {preset_name}")
    else:
        await set_preset_cmd.finish(f"不存在的预设: {preset_name}")


@reload_meme_cmd.handle()
async def _():
    await _memes.load_all()
    await reload_meme_cmd.finish(f"已重载。管理员: {len(_memes._admin_memes)} 个, 自动收集: {len(_memes._collected_memes)} 个")


@auto_chat.handle()
async def handle_auto_chat(bot: Bot, event: Event):
    if not _maybe_enabled(bot, event):
        return
    session = await resolve_session(bot, event)
    if session is None or not is_enabled(session, plugin_config):
        return
    user_id = _user_id(event)
    if user_id and user_id == str(bot.self_id):
        return
    # 只跳过 invoker 自己合成的事件（否则自己发起的命令会被当成用户重复发送）
    if _invoker and _invoker.is_synthetic(event):
        return
    logger.success(f"[接收] {session.key} 收到消息 from {user_id}")

    processor = _get_processor(session.key)

    # 先加入 chunk（占位），并设置计时器/bot/event。
    # 防止解析期间插件响应先入 chunk，批处理用 last_time=0 立即触发，导致用户消息被拆到下一批
    # 占位带上即时可得的内容（只拼文本段，不调任何 API），含图片则标注解析中：
    # 这样即便批处理在解析期间触发，LLM 也不会收到空消息
    unimsg = await build_unimsg(bot, event)
    instant = unimsg.extract_plain_text() if unimsg is not None else ""
    if unimsg is not None and any(
        isinstance(seg, Image) or image_data(seg) is not None for seg in unimsg
    ):
        instant += "\n[图片解析中]\n"

    msg = ChatMessage(
        time=datetime.now(),
        user_name=user_id,
        user_id=user_id,
        content=instant,
        is_at_only=False,
    )
    async with _get_lock(session.key):
        if session.key not in _group_chunks:
            _group_chunks[session.key] = []
        _group_chunks[session.key].append(msg)
        _group_last_time[session.key] = asyncio.get_event_loop().time()
        _group_bot[session.key] = bot
        _group_event[session.key] = event
        # 解析可能挂几十秒（VLM），身份要先就位，否则期间触发的插件调用会用上一个人的身份
        processor._session = session
        processor._session_event = event
        processor._current_user_id = user_id

    # 锁外异步解析消息内容（含图片下载/VLM 等慢操作）
    if unimsg is None:
        content, is_at_only = "", False
    else:
        content, is_at_only = await _parse_message(bot, session, unimsg, processor.bot_name)

    if not content:
        # 无有效内容，移除占位消息
        async with _get_lock(session.key):
            chunk = _group_chunks.get(session.key)
            if chunk and msg in chunk:
                chunk.remove(msg)
        return

    # 会话身份信息来自 uninfo（昵称/群名片/群名）；取不到就回落用户 id
    nickname = session.user_name or user_id

    # 更新占位消息内容（显示名带用户 id：空格昵称/重名群友也能区分）
    msg.user_name = f"{nickname}({user_id})" if user_id else nickname
    msg.content = content
    msg.is_at_only = is_at_only

    # 自动更新昵称
    try:
        if session.user_name:
            await processor.memory.update_nickname(user_id, session.user_name)
    except Exception:
        pass

    # 自动记录群名片（按会话保存，名片为空则清除该会话记录）
    try:
        await processor.memory.update_card(user_id, session.card or "")
    except Exception:
        pass


@group_notice.handle()
async def handle_group_notice(bot: Bot, event: Event):
    """群/频道系统通知（戳一戳/禁言/进出群/消息撤回等）→ 渲染后入缓冲区

    通知事件没有用户消息上下文：不更新 processor._current_user_id（避免污染工具调用身份），
    批处理触发依赖该会话最近一次真实用户消息（与 peer 推送的等待行为一致）
    """
    info = await resolve_session(bot, event)
    if info is None or info.is_private or not is_enabled(info, plugin_config):
        return
    try:
        text = await notice_text(bot, info, event)
    except Exception as e:
        logger.error(f"[通知] 事件渲染失败: {e}")
        return
    if not text:
        return
    logger.info(f"[通知] {info.key}: {text}")
    _add_notice_message(info.key, text)


# ========== 启动 ==========

@get_driver().on_startup
async def _on_startup():
    logger.success(f"[启动] 图片理解模式: {plugin_config.aigfm_image_mode}")
    anime_backend = effective_backend()
    if anime_backend == "off":
        logger.info("[启动] 二次元角色识别: 未启用")
    else:
        logger.success(f"[启动] 二次元角色识别: {anime_backend}")
        if anime_backend in ("animetrace", "both"):
            # 预热 AnimeTrace 模型列表（后台任务，失败静默）
            asyncio.create_task(_image_handler.warmup_anime())
    if plugin_config.aigfm_search_enabled:
        logger.success(f"[启动] 联网搜索: 已启用 | API: {plugin_config.aigfm_search_api}")
    else:
        logger.info("[启动] 联网搜索: 未启用")

    # 旧版裸 id 会话目录 → onebot11_ 前缀（一次性、幂等）
    migrate_legacy_dirs(_data_dir)

    await _presets.load_all()
    await _memes.load_all()
    await _memes.cleanup()
    # 启动时清一次只增不减的图片缓存：描述/角色识别缓存（JSON）与原始图片缓存（raw）
    prune_dir(_cache_dir / "image_cache", plugin_config.aigfm_image_cache_max_files, "*.json")
    prune_dir(_cache_dir / "raw", plugin_config.aigfm_raw_cache_max_files)

    # 加载命令学习器
    if plugin_config.aigfm_learn_commands:
        await _learner.load()
        logger.success(f"[启动] 命令学习: 已启用 | 最小置信度: {plugin_config.aigfm_learn_min_confidence}")

    # 注册钩子（跨插件捕获对所有适配器生效，见 api_hooks 模块说明）
    register_hooks(_bus, _image_handler, _add_external_message, _on_image_start, _on_image_done)

    # 注册跨 bot 接收端点（其它 bot 推送消息到此；peer 协议本身仍是 onebot 实例间通信）
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

    # 初始化每会话处理器并加载默认预设
    keys = _enabled_keys()
    for key in keys:
        processor = _get_processor(key)
        await processor.load_preset(plugin_config.aigfm_default_preset)
        # 启动批处理任务
        task = asyncio.create_task(_batch_processor(key))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    logger.success(
        f"[启动] 启用会话: {len(keys)} 个 "
        f"（群/频道 {len(normalize_enabled(plugin_config.aigfm_enabled_groups))}，"
        f"私聊 {len(normalize_enabled(plugin_config.aigfm_enabled_private))}）"
        + (f" | {', '.join(sorted(keys))}" if keys else "")
    )
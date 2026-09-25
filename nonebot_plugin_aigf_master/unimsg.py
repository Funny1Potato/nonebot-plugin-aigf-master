"""通用消息层：基于 nonebot_plugin_alconna 的 uniseg 做跨适配器的收发

- 收：任意适配器的消息 → `UniMessage`（通用消息段）→ 渲染成 prompt 文本
- 发：回复段/命令参数段 → `UniMessage` → 发送或导出成所在适配器的消息

各适配器的通用消息段由 alconna 提供；它未映射的段（如 onebot 的 share/contact/
location/music/dice/…）会以 `Other` 保留原始 MessageSegment，这里再用原有的
onebot 渲染逻辑兜底，保证 main 已有的消息类型渲染行为不回退。
"""

import base64
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import (
    At, AtAll, Audio, Emoji, File, Hyper, Image, Other, Reference, Reply, Text, UniMessage, Video, Voice,
)

from .session import SessionInfo, display_user, resolve_at_target

# 其余模块（api_hooks/processor）只需图片/JSON 两个提取器
__all__ = [
    "build_unimsg", "render_incoming", "render_reply", "compose_unimsg_list",
    "send_unimsg", "plain_text", "image_data", "extract_image_data",
    "extract_json_desc", "onebot_extra_text", "RenderDeps",
]

_IMAGE_ORIGIN_TYPES = ("image", "emoji")


@dataclass
class RenderDeps:
    """渲染新消息所需的回调（图片管线留在 __init__.py，避免本模块依赖存储层）"""
    bot_name: str
    render_image: Callable[[object, dict, bool], Awaitable[str]]


# ========== 通用消息段 → 原始提取 ==========

def _file_uri_to_path(uri: str) -> str:
    """file:///D:/a/b.png → D:/a/b.png；file:///tmp/a.png → /tmp/a.png"""
    path = uri[len("file://"):]
    if path.startswith("/") and len(path) > 3 and path[2] == ":":
        path = path[1:]          # Windows 的 /D:/... 形式
    return path


def extract_image_data(data: dict) -> dict:
    """从图片段落的 data 中提取图片数据，处理 base64://、file:// 与 http(s) url 前缀"""
    file_value = data.get("file", "") or ""
    url = data.get("url", "") or ""
    b64 = data.get("base64", "") or ""

    if file_value.startswith("base64://"):
        b64 = file_value[9:]
        file_value = ""
    elif file_value.startswith("file://"):
        file_value = _file_uri_to_path(file_value)
    elif file_value.startswith(("http://", "https://")) and not url:
        # MessageSegment.image(url) 时 url 会被 OneBot 适配器放在 file 字段
        url = file_value
        file_value = ""

    return {"type": "image", "url": url, "file": file_value, "base64": b64}


def _classify_image_value(value: str) -> dict:
    """适配器把图片塞在 id/一个字段里时（data URI / url / base64 / 路径）分类"""
    text = (value or "").strip()
    if not text:
        return {}
    if text.startswith("base64://"):
        return {"base64": text[9:]}
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        return {"base64": payload}
    if text.startswith("http://") or text.startswith("https://"):
        return {"url": text}
    if text.startswith("file://"):
        return {"file": _file_uri_to_path(text)}
    return {"file": text}


def _demangle_url(value: str) -> str:
    """uniseg 的 Media 会把没有 hostname 的 url 强行补成 https://（本地路径也会被补）

    这类值其实还是本地路径（如 https://D:/a.png、https://file:///D:/a.png），要还原成原值。
    """
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            rest = value[len(prefix):]
            if "\\" in rest or rest.startswith("/") or rest.startswith("file://") or (
                len(rest) > 2 and rest[1] == ":"
            ):
                return rest
    return value


def image_data(seg) -> dict | None:
    """通用 Image 段（或落进 Other 的图片类原始段）→ {"url","file","base64"}

    优先用伴随的原始段（onebot 等）的信息——适配器自己的字段最完整；统一段只作兜底。
    """
    origin = getattr(seg, "origin", None)
    if getattr(origin, "type", None) in _IMAGE_ORIGIN_TYPES:
        extracted = extract_image_data(getattr(origin, "data", {}) or {})
        if any(extracted[key] for key in ("url", "file", "base64")):
            return extracted
    if not isinstance(seg, Image):
        return None
    out = {"url": "", "file": "", "base64": ""}

    def apply(value: str):
        for key, item in _classify_image_value(_demangle_url(value)).items():
            if not out.get(key):
                out[key] = item

    if seg.url:
        apply(str(seg.url))
    if seg.path:
        out["file"] = out["file"] or str(seg.path)
    raw = getattr(seg, "raw", None)
    if raw is not None:
        if hasattr(raw, "getvalue"):
            raw = raw.getvalue()
        if raw:
            out["base64"] = out["base64"] or base64.b64encode(bytes(raw)).decode()
    if seg.id:
        apply(str(seg.id))
    if not any(out.values()):
        return None
    return out


def origin_sticker(seg) -> bool:
    """原始段里的表情包标记（onebot: sub_type/subType == 1）

    CQ 码解析出来的值可能是字符串 "1"，这里统一按字符串比较（旧实现只比 int，会漏判）。
    """
    data = getattr(getattr(seg, "origin", None), "data", {}) or {}
    value = data.get("sub_type", data.get("subType"))
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true")


def extract_json_desc(json_str) -> str:
    """从 JSON 消息中提取小程序/卡片的 title 和 desc"""
    try:
        data = json.loads(json_str) if isinstance(json_str, str) else json_str
        if isinstance(data, dict):
            title = data.get("title", "")
            desc = data.get("desc", "")
            if not title and "meta" in data:
                meta = data["meta"]
                if isinstance(meta, dict):
                    for v in meta.values():
                        if isinstance(v, dict):
                            title = v.get("title", title) or title
                            desc = v.get("desc", desc) or desc
            if title or desc:
                return f"[小程序/卡片: {', '.join(p for p in (title, desc) if p)}] "
        return "[收到一条JSON消息] "
    except (json.JSONDecodeError, TypeError):
        return "[收到一条JSON消息] "


# ========== alconna 未映射段的兜底渲染（沿用 main 的既有文案） ==========

def _size_text(file_size) -> str:
    try:
        size_bytes = int(file_size)
    except (TypeError, ValueError):
        return ""
    if size_bytes >= 1024 * 1024:
        return f"（{size_bytes / 1024 / 1024:.1f}MB）"
    if size_bytes >= 1024:
        return f"（{size_bytes / 1024:.1f}KB）"
    return f"（{size_bytes}B）"


def onebot_extra_text(seg) -> str:
    """`Other` 段渲染：优先按原始段（onebot）信息出文案，否则给通用占位"""
    origin = getattr(seg, "origin", None)
    seg_type = getattr(origin, "type", None)
    data = getattr(origin, "data", {}) or {}
    if seg_type == "share":
        parts = [p for p in (data.get("title", ""), data.get("url", ""), data.get("content")) if p]
        return f"[分享链接: {' | '.join(parts)}] " if parts else "[分享链接] "
    if seg_type == "contact":
        kind = "群" if data.get("type") == "group" else "好友"
        return f"[推荐{kind}: {data.get('id', '')}] " if data.get("id") else f"[推荐{kind}] "
    if seg_type == "location":
        parts = [data["title"]] if data.get("title") else []
        if data.get("lat") and data.get("lon"):
            parts.append(f"{data['lat']},{data['lon']}")
        if data.get("content"):
            parts.append(data["content"])
        return f"[位置: {' | '.join(parts)}] " if parts else "[位置] "
    if seg_type == "music":
        if data.get("title"):
            return f"[音乐分享: {data['title']}] "
        return f"[音乐分享 {data.get('id', '')}] "
    if seg_type == "dice":
        return f"[骰子 {data['result']}] " if data.get("result") else "[骰子] "
    if seg_type in ("rps", "shake", "anonymous", "node"):
        label = {"rps": "猜拳", "shake": "窗口抖动", "anonymous": "匿名消息", "node": "合并聊天记录节点"}[seg_type]
        return f"[{label}] "
    if seg_type == "file":
        name = data.get("file_name") or data.get("name")
        size = _size_text(data.get("file_size") or data.get("size"))
        return f"[群文件: {name}{size}] " if name else "[群文件] "
    if seg_type:
        return f"[其他消息段: {seg_type}] "
    return "[未知消息段] "


def _onebot_face_name(seg) -> str:
    """onebot 的 face 段可能带表情名（text），缺失时用 id"""
    data = getattr(getattr(seg, "origin", None), "data", {}) or {}
    return str(data.get("text") or data.get("id") or "")


def _file_text(seg) -> str:
    """群文件：原始段有大小信息时用完整文案，否则只用通用段的名字"""
    origin = getattr(seg, "origin", None)
    if getattr(origin, "type", None) == "file":
        return onebot_extra_text(seg)
    return f"[群文件: {seg.name}] " if getattr(seg, "name", "") else "[群文件] "


# ========== 接收 ==========

async def build_unimsg(bot: Bot, event: Event) -> UniMessage | None:
    """事件消息 → 通用消息；适配器不受支持时返回 None（调用方回落纯文本）"""
    try:
        message = event.get_message()
    except Exception as e:
        logger.debug(f"[消息] 取消息失败: {e}")
        return None
    try:
        unimsg = UniMessage.of(message, bot=bot)
    except Exception as e:
        logger.debug(f"[消息] {bot.adapter.get_name()} 通用化失败: {e}")
        return None
    try:
        # 补上被回复消息的完整内容（部分适配器会把回复消息一起带在事件里）
        await unimsg.attach_reply(event, bot)
    except Exception as e:
        logger.debug(f"[消息] 补充回复段失败: {e}")
    return unimsg


def _sender_label(sender) -> str:
    """从 sender（对象或 dict）里取「昵称(用户id)」标签

    只接受**字符串**取值：部分适配器（如 Kaiheila）原始回复段上的 sender 是对象/方法甚至占位值，
    直接 `value.strip()` 会抛 TypeError 把整条消息的渲染打挂（实测踩过）。
    """
    if sender is None:
        return ""

    def pick(obj, name: str) -> str:
        try:
            value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        except Exception:
            return ""
        return value.strip() if isinstance(value, str) else ""

    def pick_id(obj, name: str) -> str:
        """用户 id 可能是 int（onebot）也可能是 str（多数平台），只拒掉非标量"""
        try:
            value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        except Exception:
            return ""
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return ""
        return str(value).strip()

    nick = pick(sender, "nickname") or pick(sender, "name")
    uid = pick_id(sender, "user_id")
    if uid and nick:
        return f"{nick}({uid})"
    return nick or uid


async def render_reply(bot: Bot, session: SessionInfo | None, seg: Reply, deps: RenderDeps) -> str:
    """回复段 → `[回复 xxx 的消息: "..."]`（被回复消息递归走同一套渲染）"""
    origin = getattr(seg, "origin", None)
    label = _sender_label(getattr(origin, "sender", None))
    raw = getattr(seg, "msg", None)
    if isinstance(raw, str) and raw.strip():
        try:
            raw = bot.adapter.get_message_class()(raw)
        except Exception:
            pass
    if raw is None:
        # 适配器没带被回复消息：onebot 等可以用 API 反查
        getter = getattr(bot, "get_msg", None)
        if getter and str(seg.id).isdigit():
            try:
                original = await getter(message_id=int(seg.id))
                if isinstance(original, dict):
                    raw = original.get("message")
                    label = label or _sender_label(original.get("sender"))
            except Exception as e:
                logger.warning(f"回复消息处理失败: {e}")
    if raw is None:
        return f"[回复 {label} 的消息: （无法获取被回复消息）] " if label else "[回复: （无法获取被回复消息）] "
    unimsg = None
    try:
        unimsg = UniMessage.of(raw, bot=bot) if not isinstance(raw, UniMessage) else raw
    except Exception as e:
        logger.debug(f"[消息] 被回复消息通用化失败: {e}")
    if unimsg is None:
        return f"[回复 {label} 的消息: \"{str(raw)[:50]}\"] " if label else f"[回复: \"{str(raw)[:50]}\"] "
    text, _ = await render_incoming(bot, session, unimsg, deps, in_reply=True)
    if not text:
        text = "（非文字消息）"
    return f"[回复 {label} 的消息: \"{text}\"] " if label else f"[回复: \"{text}\"] "


async def render_incoming(bot: Bot, session: SessionInfo | None, unimsg: UniMessage,
                          deps: RenderDeps, *, in_reply: bool = False) -> tuple[str, bool]:
    """通用消息 → prompt 文本；返回 (content, is_at_only)"""
    content = ""
    has_non_at = False

    for seg in unimsg:
        if isinstance(seg, Text):
            text = seg.text or ""
            content += text
            if text.strip():
                has_non_at = True
        elif isinstance(seg, Reply):
            if in_reply:
                continue
            has_non_at = True
            content += await render_reply(bot, session, seg, deps)
        elif isinstance(seg, AtAll):
            # 只 @ 别人 / @全体，不算「有实际内容」——这类消息会多等一会儿（可能还有后续输入）
            content += " @全体成员 "
        elif isinstance(seg, At):
            target = str(seg.target or "")
            if getattr(seg, "flag", "user") != "user":
                content += f" @[{seg.flag}:{target}] "
            elif target and target == str(bot.self_id):
                content += f" @{deps.bot_name} "
            elif target:
                content += f" @{await display_user(bot, session, target)}({target}) "
        elif isinstance(seg, Emoji):
            has_non_at = True
            name = (seg.name or "").strip() or _onebot_face_name(seg)
            content += f"[QQ表情 {name}] " if name else "[QQ表情] "
        elif isinstance(seg, Image) or (isinstance(seg, Other) and image_data(seg) is not None):
            has_non_at = True
            data = image_data(seg)
            if data is None:
                content += "[图片] "
                continue
            is_sticker = origin_sticker(seg) or bool(getattr(seg, "sticker", False))
            content += await deps.render_image(seg, data, is_sticker)
        elif isinstance(seg, Reference):
            has_non_at = True
            content += "[收到一条合并聊天记录] "
        elif isinstance(seg, Hyper):
            has_non_at = True
            content += extract_json_desc(seg.raw or "") if seg.format == "json" else "[收到一条XML消息] "
        elif isinstance(seg, File):
            has_non_at = True
            content += _file_text(seg)
        elif isinstance(seg, (Voice, Audio)):
            has_non_at = True
            content += "[收到一条语音消息] "
        elif isinstance(seg, Video):
            has_non_at = True
            content += "[收到一条视频消息] "
        elif isinstance(seg, Other):
            has_non_at = True
            content += onebot_extra_text(seg)
        else:
            has_non_at = True
            content += f"[{type(seg).__name__}] "

    return content.strip(), not has_non_at


# ========== 发送 ==========

def plain_text(unimsg: UniMessage) -> str:
    """只取文本段（合成事件的 raw_message 用）"""
    return "".join(seg.text for seg in unimsg if isinstance(seg, Text))


async def compose_unimsg_list(bot: Bot, session: SessionInfo | None,
                             parts: list[dict]) -> tuple[list[UniMessage], list[str]]:
    """把抽象段落拼成待发送的通用消息列表（按顺序依次发送）

    parts 元素：
      {"type": "text",  "content": str}
      {"type": "at",    "target": 昵称或用户 id（数字视为已解析，跳过查询）}
      {"type": "image", "path": 本地图片路径}

    条目之间由本函数固定插入**一个**空格；文本段的**首尾空白会被去掉**，
    这样即使 LLM 自己多写了空格也不会出现双空格（双空格会让命令参数解析失败）。
    图片各自独立成条（它前后的文本各成一条）。at 解析不出时跳过该段，并把目标记入 skipped。
    """
    messages: list[UniMessage] = []
    skipped: list[str] = []
    pending = UniMessage()

    def add(segment):
        nonlocal pending
        if pending:
            pending.append(Text(" "))
        pending.append(segment)

    def flush():
        nonlocal pending
        if pending:
            messages.append(pending)
            pending = UniMessage()

    for part in parts:
        ptype = part.get("type")
        if ptype == "text":
            content = (part.get("content") or "").strip()
            if content:
                add(Text(content))
        elif ptype == "at":
            target = part.get("target")
            if isinstance(target, int):
                uid = str(target) if target else ""
            else:
                uid = await resolve_at_target(bot, session, target) or ""
            if uid:
                add(At("user", uid))
            else:
                skipped.append(str(target or ""))
        elif ptype == "image":
            path = part.get("path")
            if path:
                flush()
                messages.append(UniMessage([Image(path=str(path))]))
    flush()
    return messages, skipped


async def send_unimsg(bot: Bot, target, unimsg: UniMessage, **kwargs):
    """发送通用消息（target 为 Target 或事件；两者都需要显式传 bot 才能脱离响应器上下文）"""
    return await unimsg.send(target=target, bot=bot, **kwargs)
"""消息组装：回复发送与插件调用（合成事件）共用同一套 @ 解析与段落拼装

段结构与 LLM 输出的 `reply` 字段一致：`{"type": "text", "content": ...}` /
`{"type": "at", "name": ...}`。两条链路原先各写一套 @ 处理（回复按昵称、调用要 QQ 号），
统一到这里后都接受「昵称或 QQ 号」，且条目之间同样以空格分隔。
"""
from typing import NamedTuple

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment


class Composed(NamedTuple):
    messages: list[Message]     # 按顺序依次发送的消息（图片各自独立成条）
    skipped_ats: list[str]      # 解析不出目标的 @（供调用方决定是否提示）


async def _get_group_members(bot: Bot, group_id: int) -> list[dict] | None:
    """获取群成员列表；失败时记录一次错误并返回 None"""
    try:
        return await bot.get_group_member_list(group_id=group_id)
    except Exception as e:
        logger.error(f"获取群成员列表失败: {e}")
        return None


def _member_names(member: dict) -> list[str]:
    """成员可用于 @ 的名称：群名片、QQ 昵称和 QQ 号"""
    names: list[str] = []
    for key in ("card", "nickname"):
        name = str(member.get(key) or "").strip()
        if name and name not in names:
            names.append(name)
    user_id = member.get("user_id")
    if user_id is not None and str(user_id) not in names:
        names.append(str(user_id))
    return names


def _resolve_at_in_members(target, members: list[dict] | None) -> int | None:
    """在已获取的成员列表中把 @ 目标规范成 QQ 号"""
    if target is None:
        return None
    text = str(target).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text) or None  # 0 不是有效 QQ，按「无目标」处理
    for member in members or []:
        if text in _member_names(member):
            user_id = member.get("user_id")
            return int(user_id) if user_id is not None else None
    return None


async def resolve_at_target(bot: Bot, group_id: int, target) -> int | None:
    """把 @ 目标规范成 QQ 号

    - 数字 / 数字字符串 → 直接使用（不发 API 请求）
    - 其余按群名片、QQ 昵称或 QQ 号精确匹配群成员列表
    解析不出（含成员不存在、API 异常）返回 None，降级方式由调用方决定。
    """
    if target is None:
        return None
    text = str(target).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text) or None  # 0 不是有效 QQ，按「无目标」处理
    return _resolve_at_in_members(text, await _get_group_members(bot, group_id))


def _text_mention_targets(members: list[dict] | None) -> list[tuple[str, int]]:
    """生成文本 @ 可供匹配的（名称, QQ号），按名称长度降序避免短名称抢先匹配"""
    targets: dict[str, int | None] = {}
    for member in members or []:
        user_id = member.get("user_id")
        uid = int(user_id) if user_id is not None else None
        for name in _member_names(member):
            # 同名成员无法确定 @ 的是谁，不自动转换，避免误 @
            targets[name] = uid if name not in targets else (
                targets[name] if targets[name] == uid else None
            )
    return sorted(
        ((name, uid) for name, uid in targets.items() if name and uid),
        key=lambda item: len(item[0]),
        reverse=True,
    )


def _looks_like_ascii_word(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")


def _split_text_mentions(text: str, members: list[dict] | None) -> list[tuple[str, str | int]]:
    """把文本里的可识别 `@群名片/@昵称/@QQ号` 拆成文本与真实 @ 段

    只转换能精确匹配群成员的名称；未知目标和其他普通文本保持原样。英文字母、
    数字结尾的名称还要求后面不是 ASCII 单词字符，避免把 `@Alice2` 误拆成 `@Alice`。
    """
    if not text or not any(mark in text for mark in ("@", "＠")):
        return [("text", text)]
    targets = _text_mention_targets(members)
    if not targets:
        return [("text", text)]

    parts: list[tuple[str, str | int]] = []
    cursor = 0
    search_from = 0
    while True:
        at_positions = [text.find(mark, search_from) for mark in ("@", "＠")]
        at_positions = [pos for pos in at_positions if pos >= 0]
        if not at_positions:
            break
        start = min(at_positions)
        matched: tuple[str, int] | None = None
        for name, uid in targets:
            end = start + 1 + len(name)
            if not text.startswith(name, start + 1):
                continue
            if (_looks_like_ascii_word(name[-1]) and end < len(text)
                    and _looks_like_ascii_word(text[end])):
                continue
            matched = (name, uid)
            break
        if matched is None:
            search_from = start + 1
            continue
        name, uid = matched
        if start > cursor:
            parts.append(("text", text[cursor:start]))
        parts.append(("at", uid))
        cursor = start + 1 + len(name)
        search_from = cursor
    if cursor < len(text):
        parts.append(("text", text[cursor:]))
    return parts or [("text", text)]


async def compose_messages(bot: Bot, group_id: int, parts: list[dict]) -> Composed:
    """把抽象段落拼成待发送的消息列表（按顺序依次发送）

    parts 元素：
      {"type": "text",  "content": str}
      {"type": "at",    "target": 昵称或 QQ 号（int 视为已解析，跳过查询）}
      {"type": "image", "path": 本地图片路径}

    条目之间由本函数固定插入**一个**空格；文本段的**首尾空白会被去掉**，
    这样即使 LLM 自己多写了空格也不会出现双空格（双空格会让命令参数解析失败）。
    图片各自独立成条（它前后的文本各成一条）。文本里模型手写的 @ 也会尽量转换为
    真实 QQ @；at 解析不出时跳过该段，并把目标记入 skipped_ats。
    """
    messages: list[Message] = []
    skipped_ats: list[str] = []
    pending = Message()
    group_members: list[dict] | None = None
    members_loaded = False

    async def get_members() -> list[dict] | None:
        nonlocal group_members, members_loaded
        if not members_loaded:
            members_loaded = True
            group_members = await _get_group_members(bot, group_id)
        return group_members

    def add(segment):
        nonlocal pending
        if pending:
            pending.append(MessageSegment.text(" "))
        pending.append(segment)

    def flush():
        nonlocal pending
        if pending:
            messages.append(pending)
            pending = Message()

    for part in parts:
        ptype = part.get("type")
        if ptype == "text":
            content = (part.get("content") or "").strip()
            if content:
                if any(mark in content for mark in ("@", "＠")):
                    members = await get_members()
                    for piece_type, value in _split_text_mentions(content, members):
                        if piece_type == "at":
                            add(MessageSegment.at(value))
                        else:
                            value = str(value).strip()
                            if value:
                                add(MessageSegment.text(value))
                else:
                    add(MessageSegment.text(content))
        elif ptype == "at":
            target = part.get("target")
            if isinstance(target, int):
                uid = target or None
            else:
                target_text = str(target or "").strip()
                uid = int(target_text) if target_text.isdigit() else None
                if uid is None:
                    uid = _resolve_at_in_members(target_text, await get_members())
            if uid:
                add(MessageSegment.at(uid))
            else:
                skipped_ats.append(str(target or ""))
        elif ptype == "image":
            path = part.get("path")
            if path:
                flush()
                messages.append(Message(MessageSegment.image(path)))
    flush()
    return Composed(messages, skipped_ats)

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


async def resolve_at_target(bot: Bot, group_id: int, target) -> int | None:
    """把 @ 目标规范成 QQ 号

    - 数字 / 数字字符串 → 直接使用（不发 API 请求）
    - 其余按群昵称精确匹配群成员列表
    解析不出（含成员不存在、API 异常）返回 None，降级方式由调用方决定。
    """
    if target is None:
        return None
    text = str(target).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text) or None  # 0 不是有效 QQ，按「无目标」处理
    try:
        members = await bot.get_group_member_list(group_id=group_id)
        for m in members:
            if m.get("nickname", "").strip() == text:
                return m["user_id"]
    except Exception as e:
        logger.error(f"获取群成员列表失败: {e}")
    return None


async def compose_messages(bot: Bot, group_id: int, parts: list[dict]) -> Composed:
    """把抽象段落拼成待发送的消息列表（按顺序依次发送）

    parts 元素：
      {"type": "text",  "content": str}
      {"type": "at",    "target": 昵称或 QQ 号（int 视为已解析，跳过查询）}
      {"type": "image", "path": 本地图片路径}

    条目之间由本函数固定插入**一个**空格；文本段的**首尾空白会被去掉**，
    这样即使 LLM 自己多写了空格也不会出现双空格（双空格会让命令参数解析失败）。
    图片各自独立成条（它前后的文本各成一条）。at 解析不出时跳过该段，并把目标记入 skipped_ats。
    """
    messages: list[Message] = []
    skipped_ats: list[str] = []
    pending = Message()

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
                add(MessageSegment.text(content))
        elif ptype == "at":
            target = part.get("target")
            if isinstance(target, int):
                uid = target or None
            else:
                uid = await resolve_at_target(bot, group_id, target)
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
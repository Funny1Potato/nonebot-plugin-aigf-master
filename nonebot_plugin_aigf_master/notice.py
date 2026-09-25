"""群/频道系统通知的渲染层

NoneBot 的 `on_notice` 已经按 `event.get_type() == "notice"` 过滤（内核
`Matcher.check_rule` 里就是 `event_type == (cls.type or event_type)`），所以事件类型闸门
本身就是跨适配器的；留给本插件的是两件事：

1. 这条通知属于哪个会话 —— 交给 `session.resolve_session`（uninfo → alconna Target 回落）
2. 渲染成一行中文 —— OneBot V11 走下面这份最详细的分支；其它适配器用一张类别关键词表，
   映射不到才回落到适配器自带的事件描述/事件名。

多数适配器的 `get_event_description()` 是整段 `model_dump` 的 JSON，直接进 prompt 会灌水，
所以这里一律丢弃 dump 形态的描述（只有 kritor/minecraft/efchat 这类真正写了句子的才采用）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import (
    GroupBanNoticeEvent, GroupDecreaseNoticeEvent, GroupIncreaseNoticeEvent,
    GroupRecallNoticeEvent, PokeNotifyEvent,
)

from .session import SessionInfo, display_user

# OneBot V11 有结构化字段（sub_type/operator_id/target_id），文案做得最细
_ONEBOT_NOTICE_TYPES = (
    PokeNotifyEvent, GroupBanNoticeEvent, GroupIncreaseNoticeEvent,
    GroupDecreaseNoticeEvent, GroupRecallNoticeEvent,
)

# 「会话线索」的字段/键名（比较前统一规范化：去掉非字母数字并小写，因此
# group_id / groupId / chat_id / chatId 都等价）。命中且取值非空即认为不是纯好友/私聊类通知。
_SCENE_FIELDS = frozenset({
    "groupid", "channelid", "guildid", "chatid", "roomid",
    "group", "channel", "guild", "chat", "room", "scene",
})

# 递归查找的深度与每个容器的取样条数（只为便宜预筛，不需要穷举）
_SCENE_DEPTH = 3
_SCENE_SAMPLE = 8

# 递归时跳过的字段：消息体/回复体又大又不会带会话线索
_SKIP_FIELDS = frozenset({"message", "original_message", "raw_message", "reply"})

# 通用主体 id 的候选路径（`event.get_user_id()` 拿不到时依次尝试；整数表示列表下标）。
# 列表型（Telegram 的 new_chat_members[0] / left_chat_member）要排在通用字段前，否则会拿错人
_SUBJECT_PATHS = (
    ("new_chat_members", 0, "id"), ("left_chat_member", "id"),
    ("user_id",), ("user", "id"), ("member", "id"), ("member", "user", "id"),
    ("target_id",), ("operator_id",), ("operator", "id"), ("member_id",), ("uin",), ("from_id",),
)

# 类别关键词表：`type(event).__name__ + get_event_name()` 规范化（只留小写字母数字）后按序匹配，
# 关键词自身也已是规范化形态。命中的模板即渲染文案，`{who}` 换主体昵称（拿不到则留空）。
# 顺序有意义：消息撤回要排在「成员删除/配置变更」之前，bilibili 直播类要排在通用类和之前。
_NOTICE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("poke", "nudge"), "{who} 被戳了戳"),
    (("recall", "messagedelete", "messagemdelete", "deleterecall"), "{who} 撤回了一条消息"),
    (("userenter", "userenterv"), "{who} 进入了直播间"),
    (("userfollow",), "{who} 关注了直播间"),
    (("guardbuy", "sendgift", "specialgift"), "{who} 赠送了礼物"),
    (("wholeban", "liftban", "unmute", "unban", "mute", "ban", "silent"), "{who} 的禁言状态发生了变化"),
    (
        (
            "memberincrease", "memberadded", "memberadd", "memberjoin", "newchatmember",
            "newmember", "groupjoin", "useradded", "join", "increase", "added", "enter",
        ),
        "{who} 加入了群聊",
    ),
    (
        (
            "memberdecrease", "memberremoved", "memberremove", "memberleave", "memberquit",
            "memberkick", "leftchatmember", "groupmemberdeleted", "groupmemberwithdrawn",
            "groupdecrease", "leave", "quit", "kick", "remove", "decrease", "deleted", "withdrawn",
        ),
        "{who} 离开了群聊",
    ),
    (("adminchange", "adminchanged", "permissionchange", "roleadd", "roledelete", "roleupdate"),
     "{who} 的管理权限发生了变化"),
    (("reaction", "reactmessage"), "{who} 对消息添加了表情回应"),
    (("essence", "pin", "pinned"), "消息的置顶/精华状态发生了变化"),
    (("groupnamechange", "roomchangeinfo", "chatphoto", "chattitle", "namechange",
      "configupdated", "cardchanged", "specialtitle", "updated", "change"),
     "会话信息发生了变化"),
)

# 描述形态的 JSON dump 一律不用（`str(model_dump(...))` / `str(self.dict())` 都以 `{` 开头）
_DESCRIPTION_MAX = 200


def is_group_notice(event: Event) -> bool:
    """`on_notice` 的规则：只看事件里有没有会话线索，不发任何请求

    真正的「是否启用/是否私聊」判断在 handler 里做（需要 uninfo，可能有 API 调用）。
    这里必须**递归**找：多数适配器把会话 id 放在顶层（`group_id`/`channel_id`…），但
    Feishu 在 `event.event.chat_id`、Milky 在 `data.group_id`、YunHu 在 `event.chatId`。
    放宽的代价只是多查一次 uninfo（handler 还会按 `is_private` 复核），漏判才会真的丢通知。
    """
    return _has_scene_hint(event, _SCENE_DEPTH)


def _has_scene_hint(value, depth: int) -> bool:
    if depth < 0 or value is None or isinstance(value, (str, bytes, int, float, bool)):
        return False
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _norm_key(key) in _SCENE_FIELDS and item:
                return True
            if _has_scene_hint(item, depth - 1):
                return True
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_has_scene_hint(item, depth - 1) for item in list(value)[:_SCENE_SAMPLE])
    fields = getattr(value, "model_fields", None)
    if fields is not None:  # pydantic 模型（事件/嵌套模型）
        for name in fields:
            if name.startswith("_") or name in _SKIP_FIELDS:
                continue
            try:
                item = getattr(value, name, None)
            except Exception:
                continue
            if _norm_key(name) in _SCENE_FIELDS and item:
                return True
            if _has_scene_hint(item, depth - 1):
                return True
        extra = getattr(value, "__pydantic_extra__", None)
        return bool(extra) and _has_scene_hint(extra, depth - 1)
    return False


def _norm_key(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


async def notice_text(bot: Bot, info: SessionInfo, event: Event) -> str | None:
    """把一条通知渲染成一行文本；渲染不出返回 None（调用方跳过该条）"""
    if isinstance(event, _ONEBOT_NOTICE_TYPES):
        return await _onebot_notice_text(bot, info, event)
    return await _generic_notice_text(bot, info, event)


async def _onebot_notice_text(bot: Bot, info: SessionInfo, event: Event) -> str | None:
    async def _name(user_id) -> str:
        name = await display_user(bot, info, user_id)
        return name or str(user_id)

    if isinstance(event, PokeNotifyEvent):
        return f"{await _name(event.user_id)} 戳了戳 {await _name(event.target_id)}"
    if isinstance(event, GroupBanNoticeEvent):
        if event.sub_type == "self_ban":
            return f"我被禁言 {event.duration} 分钟"
        if event.sub_type == "self_lift_ban":
            return "我被解除禁言"
        if event.sub_type == "lift_ban":
            return f"{await _name(event.operator_id)} 解除了 {await _name(event.user_id)} 的禁言"
        return f"{await _name(event.operator_id)} 将 {await _name(event.user_id)} 禁言 {event.duration} 分钟"
    if isinstance(event, GroupIncreaseNoticeEvent):
        return f"{await _name(event.user_id)} 加入了群聊"
    if isinstance(event, GroupDecreaseNoticeEvent):
        if event.sub_type == "kick_me":
            return "我被移出了群聊"
        if event.sub_type == "kick":
            return f"{await _name(event.user_id)} 被 {await _name(event.operator_id)} 移出了群聊"
        return f"{await _name(event.user_id)} 退出了群聊"
    if isinstance(event, GroupRecallNoticeEvent):
        if event.user_id == event.operator_id:
            return f"{await _name(event.user_id)} 撤回了一条消息"
        return f"{await _name(event.operator_id)} 撤回了 {await _name(event.user_id)} 的一条消息"
    return None


async def _generic_notice_text(bot: Bot, info: SessionInfo, event: Event) -> str | None:
    event_name = ""
    try:
        event_name = str(event.get_event_name() or "")
    except Exception:
        pass
    key = re.sub(r"[^a-z0-9]+", "", f"{type(event).__name__} {event_name}".lower())
    for keywords, template in _NOTICE_RULES:
        if any(kw in key for kw in keywords):
            return _fill(template, await _subject_name(bot, info, event))
    return _prose_description(event, event_name) or event_name or None


def _fill(template: str, who: str) -> str:
    text = template.replace("{who}", who).strip()
    return re.sub(r"\s{2,}", " ", text)


async def _subject_name(bot: Bot, info: SessionInfo, event: Event) -> str:
    """通知主体（被禁言/被移出/撤回消息的那位）的显示名"""
    uid = _subject_id(event)
    if not uid:
        return ""
    return await display_user(bot, info, uid) or uid


def _subject_id(event: Event) -> str:
    try:
        uid = event.get_user_id()
    except Exception:
        uid = ""
    if uid:
        return str(uid)
    for path in _SUBJECT_PATHS:
        obj: object = event
        for attr in path:
            if isinstance(attr, int):
                obj = obj[attr] if isinstance(obj, (list, tuple)) and len(obj) > attr else None
            else:
                obj = getattr(obj, attr, None)
            if obj is None:
                break
        if obj not in (None, ""):
            return str(obj)
    return ""


def _prose_description(event: Event, event_name: str) -> str | None:
    """适配器自带的事件描述；只有像句子的才用（dump 形态与空描述丢弃）"""
    try:
        desc = str(event.get_event_description() or "").strip()
    except Exception:
        return None
    if not desc or desc == event_name or len(desc) > _DESCRIPTION_MAX:
        return None
    if desc.startswith("{") or desc.startswith("["):
        return None
    return desc
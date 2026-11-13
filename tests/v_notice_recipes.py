"""Real adapter notice-event fixtures retained from the historical offline checks.

RECIPES and RULE_REJECTED are historical fixture group names, not current
support promises. The notice matrix evaluates both against the current rule.
Run v_notice_adapters_test.py for the current behavior.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from typing import Callable

from nonebot.adapters import Event

NOW = datetime.now()
_TS = int(NOW.timestamp())
_TS_MS = int(NOW.timestamp() * 1000)


# ============================== RECIPES（已实测通过） ==============================

def onebot_v11() -> Event:
    """群成员增加（notice_type=group_increase，顶层带 group_id）"""
    from nonebot.adapters.onebot.v11 import GroupIncreaseNoticeEvent
    return GroupIncreaseNoticeEvent(
        time=_TS, self_id=10000, post_type="notice", notice_type="group_increase",
        sub_type="approve", user_id=20001, group_id=11001, operator_id=10001)


def onebot_v12() -> Event:
    """群成员增加（detail_type=group_member_increase，顶层带 group_id）"""
    from nonebot.adapters.onebot.v12 import BotSelf
    from nonebot.adapters.onebot.v12.event import GroupMemberIncreaseEvent
    return GroupMemberIncreaseEvent(
        id="ev-ob12", time=NOW, type="notice", detail_type="group_member_increase",
        sub_type="approve", self=BotSelf(platform="qq", user_id="10000"),
        group_id="11001", user_id="20001", operator_id="10001")


def satori() -> Event:
    """公会成员加入（顶层带 guild / channel）"""
    cls = importlib.import_module("nonebot.adapters.satori.event").GuildMemberAddedEvent
    return cls.model_validate({
        "type": "guild-member-added",
        "timestamp": _TS_MS,
        "login": {"sn": 1, "status": 1, "adapter": "satori", "platform": "qq",
                  "user": {"id": "10000", "name": "bot"}},
        "guild": {"id": "g-1", "name": "公会"},
        "channel": {"id": "c-1", "type": 0, "name": "频道"},
        "user": {"id": "u-1", "name": "张三", "nick": "三儿"},
    })


def telegram() -> Event:
    """新成员进群（顶层带 chat）"""
    from nonebot.adapters.telegram.event import NewChatMemberEvent
    return NewChatMemberEvent(
        message_id=1, date=_TS,
        chat={"id": -100, "type": "group", "title": "群"},
        **{"from": {"id": 10001, "is_bot": False, "first_name": "李"}},
        new_chat_members=[{"id": 20001, "is_bot": False, "first_name": "张",
                           "last_name": "三", "username": "zhangsan"}])


def discord() -> Event:
    """公会成员加入（顶层带 guild_id）"""
    from nonebot.adapters.discord import GuildMemberAddEvent
    return GuildMemberAddEvent(
        guild_id=10, roles=[], joined_at=NOW, flags=0,
        user={"id": 3, "username": "张三", "discriminator": "0", "avatar": None},
        nick=None, deaf=False, mute=False, pending=False)


def qq() -> Event:
    """频道成员加入（顶层带 guild_id）"""
    from nonebot.adapters.qq import GuildMemberAddEvent
    return GuildMemberAddEvent(
        guild_id="g-1", op_user_id="10000", joined_at=NOW,
        user={"id": "u-1", "username": "张三", "avatar": None, "bot": False},
        nick=None, roles=[])


def mirai() -> Event:
    """群成员加入（顶层带 group；校验器要求 relationship 模型，故 model_construct）"""
    from nonebot.adapters.mirai.event import MemberJoinEvent
    from nonebot.adapters.mirai.model.relationship import Group, Member, MemberPerm
    group = Group.model_construct(id=11001, name="群", account_perm=MemberPerm.Member)
    member = Member.model_construct(id=20001, name="张三", permission=MemberPerm.Member,
                                    group=group)
    return MemberJoinEvent.model_construct(group=group, member=member)


def kaiheila() -> Event:
    """服务器成员加入（notice_type=joined_guild，顶层带 group_id）"""
    from nonebot.adapters.kaiheila.event import GuildMemberIncreaseNoticeEvent
    return GuildMemberIncreaseNoticeEvent.model_validate({
        "post_type": "notice", "notice_type": "joined_guild",
        "channel_type": "GROUP", "type": 255, "target_id": "g-1",
        "author_id": "20001", "user_id": "20001", "content": "", "msg_id": "n1",
        "msg_timestamp": _TS_MS, "nonce": "", "group_id": 11001,
        "extra": {"type": 255, "guild_id": "g-1", "body": {"user_id": "20001"}},
    })


def dodo() -> Event:
    """成员加入语音频道（顶层带 channel_id）"""
    from nonebot.adapters.dodo.event import ChannelVoiceMemberJoinEvent
    from nonebot.adapters.dodo.models import Member, Personal
    return ChannelVoiceMemberJoinEvent(
        event_id="ev-dodo", event_type="5001", timestamp=NOW, dodo_source_id="u-1",
        island_source_id="isl-1", channel_id="ch-1",
        personal=Personal(nick_name="张三", avatar_url="http://a", sex=1),
        member=Member(nick_name="张三", join_time=NOW))


def kritor() -> Event:
    """群成员增加（顶层带 group_id）"""
    from nonebot.adapters.kritor.event import GroupMemberIncreasedNotice
    return GroupMemberIncreasedNotice(
        time=NOW, group_id=11001, operator_uin=10001, target_uin=20001)


def efchat() -> Event:
    """加入房间（onlineAdd；顶层带 channel —— EFChat 的 onlineAdd 报文本身就是扁平结构）"""
    from nonebot.adapters.efchat.event import JoinRoomEvent
    return JoinRoomEvent(
        cmd="onlineAdd", event_type="notice", time=_TS, nick="张三", head="h",
        level=105, channel="room-1", city="北京", trip="t", hash="h", userid=20001)


def bilibili_live() -> Event:
    """开启等级禁言（顶层带 room_id；校验器要求原始 data 信封，故 model_construct）"""
    from nonebot.adapters.bilibili_live.event import RoomSilentOnEvent
    return RoomSilentOnEvent.model_construct(
        room_id=12345, type="level", level=0, second=60)


RECIPES: dict[str, Callable[[], Event]] = {
    "OneBot V11": onebot_v11,
    "OneBot V12": onebot_v12,
    "Satori": satori,
    "Telegram": telegram,
    "Discord": discord,
    "QQ": qq,
    "Mirai": mirai,
    "Kaiheila": kaiheila,
    "DoDo": dodo,
    "Kritor": kritor,
    "EFChat": efchat,
    "bilibili Live": bilibili_live,
}


# ==================== 真实事件可构造、但插件的预筛规则恒为 False ====================
# 这 5 个适配器的通知事件都能用自家模型真实构造（get_type() == "notice"），
# 但会话信息全部在**嵌套字段**里，事件对象顶层没有 _SCENE_ATTRS 任何一个：
#   * Feishu  : chat_id 在 event.event.chat_id（notice 事件只有 header/event 两个字段）
#   * Milky   : group_id 在 event.data.group_id（如 GroupMemberIncreaseData）
#   * Minecraft: 只有 server_name，没有群/频道概念字段
#   * WXMP    : 只有 ToUserName/FromUserName（客服会话），没有群字段
#   * YunHu   : chatId 在 event.event.chatId（GroupNoticeDetail）
# 离线扫描佐证（`NoticeEvent` 的**全部**子类，逐个查 model_fields 是否含 6 个属性名）：
#   Feishu 44 个 / Milky 16 个 / Minecraft 5 个 / WXMP 7 个 / YunHu 7 个 —— 全部 NO_SCENE_ATTR。
# 也就是说这 5 个适配器的**所有**通知都会被 `is_group_notice` 挡在 handler 之外
# （不是构造失败，而是插件预筛的口径问题）。

def feishu_unfiltered() -> Event:
    cls = importlib.import_module("nonebot.adapters.feishu").GroupMemberUserAddedEvent
    return cls.model_validate({
        "schema": "2.0",
        "header": {"event_id": "h1", "event_type": "im.chat.member.user.added_v1",
                   "create_time": "1700000000000", "token": "t", "app_id": "a",
                   "tenant_key": "tk"},
        "event": {"chat_id": "oc-1",
                  "operator_id": {"union_id": "un0", "user_id": "u0", "open_id": "ou-bot"},
                  "external": False, "operator_tenant_key": "tk",
                  "users": [{"name": "张三", "tenant_key": "tk",
                             "user_id": {"union_id": "un1", "user_id": "u1",
                                         "open_id": "ou-1"}}]},
    })


def milky_unfiltered() -> Event:
    cls = importlib.import_module("nonebot.adapters.milky.event").GroupMemberIncreaseEvent
    return cls.model_validate({
        "time": _TS, "self_id": "10000",
        "data": {"group_id": 11001, "user_id": 20001, "operator_id": 10001},
    })


def minecraft_unfiltered() -> Event:
    from nonebot.adapters.minecraft import PlayerJoinEvent
    from nonebot.adapters.minecraft.models import Player
    return PlayerJoinEvent(
        timestamp=_TS, post_type="notice", event_name="PlayerJoinEvent",
        server_name="srv", server_version="1.20", server_type="vanilla",
        sub_type="player_join", player=Player(nickname="Steve", uuid=None))


def wxmp_unfiltered() -> Event:
    cls = importlib.import_module("nonebot.adapters.wxmp").UserEnterEvent
    return cls(**{"ToUserName": "gh_bot", "FromUserName": "user001", "CreateTime": _TS,
                  "MsgType": "event", "Event": "user_enter_tempsession",
                  "SessionFrom": "s"})


def yunhu_unfiltered() -> Event:
    from nonebot.adapters.yunhu.event import GroupJoinNoticeEvent
    return GroupJoinNoticeEvent.model_validate({
        "version": "1.0",
        "header": {"eventId": "ev-yunhu", "eventTime": str(_TS_MS),
                   "eventType": "group.join"},
        "event": {"time": _TS_MS, "chatId": "g-1", "chatType": "group",
                  "userId": "20001", "nickname": "张三", "avatarUrl": "http://a"},
    })


RULE_REJECTED: dict[str, Callable[[], Event]] = {
    "Feishu": feishu_unfiltered,
    "Milky": milky_unfiltered,
    "Minecraft": minecraft_unfiltered,
    "WXMP": wxmp_unfiltered,
    "YunHu": yunhu_unfiltered,
}


# ============================== 自检 ==============================

from pathlib import Path
PLUGIN_DIR = str(Path(__file__).resolve().parents[1])


def load_rule() -> Callable[[Event], bool]:
    """导入插件真实的 is_group_notice（走 NoneBot 插件管理器，避免 localstore 认不出调用方）"""
    import nonebot

    nonebot.init(
        driver="~none", localstore_use_cwd=True, log_level="CRITICAL",
        command_start={""}, superusers={"945126014"},
        aigfm_llm_api_key="test-key", aigfm_llm_base_url="http://127.0.0.1:9/v1",
        aigfm_vlm_enabled=False, aigfm_anime_backend="off",
        aigfm_enabled_groups=[],
    )
    sys.path.insert(0, PLUGIN_DIR)
    for dep in ("nonebot_plugin_alconna", "nonebot_plugin_uninfo"):
        try:
            nonebot.load_plugin(dep)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] load_plugin({dep}) 失败: {exc!r}")
    nonebot.load_plugin("nonebot_plugin_aigf_master")
    return importlib.import_module("nonebot_plugin_aigf_master.notice").is_group_notice


def _line(name: str, event: Event, rule: Callable[[Event], bool]) -> tuple[bool, str]:
    try:
        event_type = event.get_type()
    except Exception as exc:  # noqa: BLE001
        return False, f"{name:<14}{type(event).__name__:<32}{'<get_type failed>':<12}{exc!r}"
    ok = bool(rule(event))
    return ok, f"{name:<14}{type(event).__name__:<32}{event_type:<12}{ok}"


if __name__ == "__main__":
    rule = load_rule()
    notice_mod = sys.modules["nonebot_plugin_aigf_master.notice"]
    print(f"_SCENE_ATTRS = {getattr(notice_mod, '_SCENE_ATTRS')}\n")

    print("=== RECIPES（要求全部 notice / True）===")
    print(f"{'adapter':<14}{'event class':<32}{'get_type()':<12}is_group_notice")
    print("-" * 72)
    failures = []
    for name, recipe in RECIPES.items():
        try:
            event = recipe()
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"{name:<14}{'<construction failed>':<32}{type(exc).__name__}: {exc}")
            continue
        ok, line = _line(name, event, rule)
        print(line)
        if not ok:
            failures.append(name)

    print("\n=== RULE_REJECTED（真实事件，但插件预筛 is_group_notice == False）===")
    print(f"{'adapter':<14}{'event class':<32}{'get_type()':<12}is_group_notice")
    print("-" * 72)
    for name, recipe in RULE_REJECTED.items():
        try:
            event = recipe()
        except Exception as exc:  # noqa: BLE001
            print(f"{name:<14}{'<construction failed>':<32}{type(exc).__name__}: {exc}")
            continue
        _, line = _line(name, event, rule)
        print(line)

    if failures:
        print(f"\n[FAIL] RECIPES 中有 {len(failures)} 项未通过: {failures}")
        raise SystemExit(1)
    print(f"\n[OK] RECIPES 全部 {len(RECIPES)} 项: get_type() == 'notice' 且 is_group_notice == True")
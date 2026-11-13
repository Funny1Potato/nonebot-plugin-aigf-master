"""离线验证：群/频道系统通知的通用化（规则预筛 / OneBot 文案 / 类别映射表 / 回落 / handler 集成）"""
from _bootstrap import REPO, SOURCE, TMP, close_loop


import asyncio
import os
import shutil
import sys
from pathlib import Path

_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)
close_loop(_LOOP)


def run(coro):
    return _LOOP.run_until_complete(coro)


if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
os.chdir(TMP)

import nonebot  # noqa: E402

nonebot.init(
    driver="~none",
    localstore_use_cwd=True,
    log_level="CRITICAL",
    command_start={""},
    superusers={"945126014"},
    aigfm_llm_api_key="test-key",
    aigfm_llm_base_url="http://127.0.0.1:9/v1",
    aigfm_vlm_enabled=False,
    aigfm_anime_backend="off",
    aigfm_enabled_groups=[617770183, "onebot11:617770185"],
    aigfm_enabled_private=[20001],
    aigfm_batch_timeout=0.2,
    aigfm_incomplete_timeout=0.3,
    aigfm_batch_count=100,
)
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot.adapters import Event  # noqa: E402
from nonebot.adapters.onebot.v11 import (  # noqa: E402
    Bot as OB11Bot, GroupBanNoticeEvent, GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent, GroupRecallNoticeEvent, Message as OB11Message,
    PokeNotifyEvent,
)
from nonebot_plugin_aigf_master import notice as N  # noqa: E402
from nonebot_plugin_aigf_master import session as S  # noqa: E402
from nonebot_plugin_aigf_master.config import plugin_config  # noqa: E402
from nonebot_plugin_aigf_master.session import SessionInfo, resolve_session  # noqa: E402

FAILED = []
PASSED = 0


def check(name, actual, expected):
    global PASSED
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} | {name} | got={actual!r}" + ("" if ok else f" want={expected!r}"))
    if ok:
        PASSED += 1
    else:
        FAILED.append(name)


def check_true(name, value):
    check(name, bool(value), True)


# ========== 桩 ==========

class MockAdapter:
    def __init__(self, name="OneBot V11"):
        self._name = name
        self.config = nonebot.get_driver().config

    def get_name(self):
        return self._name

    def get_message_class(self):
        return OB11Message

    async def _call_api(self, bot, api, **data):
        return {}


MEMBERS = {
    20001: {"nickname": "张三", "card": "名片张", "role": "admin"},
    20002: {"nickname": "李四", "card": "", "role": "member"},
}


class MockBot(OB11Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []

    async def call_api(self, api, **data):
        self.calls.append((api, data))
        uid = int(data.get("user_id") or 0)
        if api == "get_group_info":
            return {"group_id": int(data.get("group_id") or 0), "group_name": "测试群"}
        if api == "get_group_member_info":
            info = MEMBERS.get(uid, {"nickname": f"用户{uid}", "card": "", "role": "member"})
            return {"user_id": uid, "sex": "male", "join_time": 1600000000, **info}
        if api == "get_group_member_list":
            return [{"user_id": u, "sex": "male", "join_time": 1600000000, **i} for u, i in MEMBERS.items()]
        if api == "get_stranger_info":
            return {"user_id": uid, "nickname": f"用户{uid}", "sex": "male"}
        return {}


def make_bot(name="OneBot V11") -> MockBot:
    return MockBot(adapter=MockAdapter(name), self_id="10000")


_base = dict(time=1700000000, self_id=10000, post_type="notice")


def poke(user_id=20001, target_id=20002, group_id=617770183):
    return PokeNotifyEvent(notice_type="notify", sub_type="poke", user_id=user_id,
                           target_id=target_id, group_id=group_id, **_base)


def increase(user_id=20002, operator_id=20001, group_id=617770183, sub_type="approve"):
    return GroupIncreaseNoticeEvent(notice_type="group_increase", sub_type=sub_type, user_id=user_id,
                                    group_id=group_id, operator_id=operator_id, **_base)


def decrease(user_id=20002, operator_id=20001, group_id=617770183, sub_type="leave"):
    return GroupDecreaseNoticeEvent(notice_type="group_decrease", sub_type=sub_type, user_id=user_id,
                                    group_id=group_id, operator_id=operator_id, **_base)


def ban(user_id=20002, operator_id=20001, group_id=617770183, sub_type="ban", duration=10):
    return GroupBanNoticeEvent(notice_type="group_ban", sub_type=sub_type, user_id=user_id,
                               group_id=group_id, operator_id=operator_id, duration=duration, **_base)


def recall(user_id=20002, operator_id=20002, group_id=617770183):
    return GroupRecallNoticeEvent(notice_type="group_recall", user_id=user_id, group_id=group_id,
                                  operator_id=operator_id, message_id=12345, **_base)


class FakeNotice(Event):
    """非 OneBot 的通知事件桩：只要实现 Event 的抽象方法即可"""

    group_id: str = ""
    user_id: str = "20002"

    def get_type(self) -> str:
        return "notice"

    def get_event_name(self) -> str:
        return str(getattr(self, "ev_name", "notice"))

    def get_event_description(self) -> str:
        return str(getattr(self, "ev_desc", ""))

    def get_user_id(self) -> str:
        return self.user_id

    def get_session_id(self) -> str:
        return f"group_{self.group_id}_{self.user_id}"

    def get_message(self):
        raise ValueError("no message")

    def is_tome(self) -> bool:
        return False


def fake_event(cls_name, ev_name="notice", desc="", user_id="20002", group_id="G1"):
    cls = type(cls_name, (FakeNotice,), {})
    return cls(ev_name=ev_name, ev_desc=desc, user_id=user_id, group_id=group_id)


FAKE_INFO = SessionInfo(key="satori:G1", slug="satori", scene_path="G1", native_chat_id="G1",
                        adapter_name="Satori")
FAKE_BOT = make_bot("Satori")


async def patched_display_user(bot, info, uid):
    return {"20002": "李四", "20001": "张三"}.get(str(uid), str(uid))


_real_display_user = N.display_user

# ========== A. 规则预筛 ==========

print("\n=== A. 规则预筛（on_notice 的 rule） ===")
check("A1 群成员增加", N.is_group_notice(increase()), True)
check("A2 群禁言", N.is_group_notice(ban()), True)
check("A3 群戳一戳", N.is_group_notice(poke()), True)
check("A4 私聊戳一戳（无群）", N.is_group_notice(poke(group_id=None)), False)
check("A5 非 OneBot 带群字段", N.is_group_notice(fake_event("GuildMemberAddEvent", group_id="G1")), True)
check("A6 非 OneBot 无群字段", N.is_group_notice(fake_event("ChannelNoticeEvent", group_id="")), False)
_ch = fake_event("ChannelNoticeEvent", group_id="")
_ch.channel = {"id": "c1"}
check("A7 带 channel 对象", N.is_group_notice(_ch), True)
check("A8 好友类通知无会话线索", N.is_group_notice(fake_event("FriendAddEvent", group_id="")), False)

# 嵌套会话线索（回归：预筛曾只查顶层属性，Feishu / Milky / YunHu 的通知会被整类丢掉）
_n1 = fake_event("FeishuStyleEvent", group_id="")
_n1.event = {"event": {"chat_id": "oc-1"}}
check("A10 嵌套 chat_id（Feishu 形态）", N.is_group_notice(_n1), True)
_n2 = fake_event("YunHuStyleEvent", group_id="")
_n2.event = {"event": {"chatId": "g-1"}}
check("A11 嵌套 camelCase chatId（YunHu 形态）", N.is_group_notice(_n2), True)
_n3 = fake_event("MilkyStyleEvent", group_id="")
_n3.data = {"group_id": "11001"}
check("A12 嵌套 data.group_id（Milky 形态）", N.is_group_notice(_n3), True)
_n4 = fake_event("MinecraftStyleEvent", group_id="")
_n4.server_name = "srv"
check("A13 只有 server_name（Minecraft 形态）→ 不收", N.is_group_notice(_n4), False)
check("A9 matcher 类型闸门", mod.group_notice.type, "notice")

# ========== B. OneBot 分支（保留最详细文案，昵称走 uninfo） ==========

print("\n=== B. OneBot 文案 ===")
bot = make_bot()


def ob11_text(event, bot_=None):
    b = bot_ or bot
    info = run(resolve_session(b, event))
    assert info is not None, "会话解析失败"
    return run(N.notice_text(b, info, event))


check("B1 成员加入", ob11_text(increase()), "李四 加入了群聊")
check("B2 戳一戳", ob11_text(poke()), "张三 戳了戳 李四")
check("B3 禁言", ob11_text(ban()), "张三 将 李四 禁言 10 分钟")
check("B4 解禁", ob11_text(ban(sub_type="lift_ban")), "张三 解除了 李四 的禁言")
check("B5 我被禁言", ob11_text(ban(user_id=10000, sub_type="self_ban")), "我被禁言 10 分钟")
check("B6 退群", ob11_text(decrease()), "李四 退出了群聊")
check("B7 被踢", ob11_text(decrease(sub_type="kick")), "李四 被 张三 移出了群聊")
check("B8 我被移出", ob11_text(decrease(user_id=10000, sub_type="kick_me")), "我被移出了群聊")
check("B9 自己撤回", ob11_text(recall()), "李四 撤回了一条消息")
check("B10 代撤回", ob11_text(recall(operator_id=20001)), "张三 撤回了 李四 的一条消息")

# ========== C. 通用类别映射表 ==========

print("\n=== C. 通用映射表 ===")
N.display_user = patched_display_user


def gen_text(cls_name, ev_name="notice", desc="", **kw):
    ev = fake_event(cls_name, ev_name, desc, **kw)
    return run(N.notice_text(FAKE_BOT, FAKE_INFO, ev))


check("C1 成员加入（Discord 形态）", gen_text("GuildMemberAddEvent"), "李四 加入了群聊")
check("C2 成员加入（Kritor 形态）", gen_text("GroupMemberIncreasedEvent", "notice.group_member_increased"),
      "李四 加入了群聊")
check("C3 成员加入（QQ 频道形态）", gen_text("GroupMemberAddEvent", "GROUP_MEMBER_ADD"), "李四 加入了群聊")
check("C4 新成员（Telegram 形态）", gen_text("NewChatMemberEvent"), "李四 加入了群聊")
check("C5 成员加入（YunHu 形态）", gen_text("GroupJoinNoticeEvent", "group.join"), "李四 加入了群聊")
check("C6 成员离开（Satori 形态）", gen_text("GuildMemberRemovedEvent"), "李四 离开了群聊")
check("C7 左成员（Telegram 形态）", gen_text("LeftChatMemberEvent"), "李四 离开了群聊")
check("C8 退群（Mirai 形态）", gen_text("MemberLeaveEvent"), "李四 离开了群聊")
check("C9 禁言（Milky 形态）", gen_text("GroupMuteEvent"), "李四 的禁言状态发生了变化")
check("C10 撤回（Kaiheila 形态）", gen_text("ChannelMessageDeleteEvent"), "李四 撤回了一条消息")
check("C11 戳一戳（Mirai 形态）", gen_text("NudgeEvent"), "李四 被戳了戳")
check("C12 表情回应（Discord 形态）", gen_text("MessageReactionAddEvent"), "李四 对消息添加了表情回应")
check("C13 管理员变更（Mirai 形态）", gen_text("MemberPermissionChangeEvent"), "李四 的管理权限发生了变化")
check("C14 置顶（Telegram 形态）", gen_text("PinnedMessageEvent"), "消息的置顶/精华状态发生了变化")
check("C15 会话设置（Feishu 形态）", gen_text("GroupConfigUpdatedEvent"), "会话信息发生了变化")
check("C16 直播进场（bilibili 形态，优先于「加入」）", gen_text("UserEnterEvent"), "李四 进入了直播间")
check("C17 直播关注（bilibili 形态）", gen_text("UserFollowEvent"), "李四 关注了直播间")
check("C18 无主体时不留占位与双空格", gen_text("GuildMemberAddEvent", user_id=""), "加入了群聊")

# ========== D. 回落策略（绝不塞 JSON dump） ==========

print("\n=== D. 回落 ===")
dump = '{"time": 1, "self_id": 2, "post_type": "notice", "group_id": "G1"}'
check("D1 dump 描述被丢弃 → 事件名", gen_text("SomeWeirdNoticeEvent", "notice.some_weird", dump),
      "notice.some_weird")
check("D2 散文描述被采用（Kritor 形态）",
      gen_text("GroupTodoEvent", "notice.group_todo", "New member 1 joined Group 2 by 3"),
      "New member 1 joined Group 2 by 3")
check("D3 名字与描述相同 → 用事件名（Mirai 形态）",
      gen_text("WeirdEvent", "WeirdEvent", "WeirdEvent"), "WeirdEvent")
check("D4 超长描述被丢弃", gen_text("LongEvent", "notice.long", "x" * 300), "notice.long")
check("D5 方括号开头的 dump 被丢弃", gen_text("BracketEvent", "notice.br", "[{'a': 1}]"), "notice.br")
check("D6 全空 → None", run(N.notice_text(FAKE_BOT, FAKE_INFO, fake_event("EmptyEvent", "", ""))), None)

# ========== D2. 主体 id 兜底（通知事件的 get_user_id 常直接抛错） ==========

print("\n=== D2. 主体兜底 ===")


class UidRaisesEvent(FakeNotice):
    def get_user_id(self):
        raise ValueError("Event has no user_id!")


_u = UidRaisesEvent.model_construct(group_id="G1", user_id="7")
check("D7 get_user_id 抛错时回落路径", N._subject_id(_u), "7")
_li = UidRaisesEvent.model_construct(group_id="G1", user_id="")
_li.new_chat_members = [type("U", (), {"id": "9"})()]
check("D8 列表型主体（Telegram new_chat_members）", N._subject_id(_li), "9")
check("D9 session.event_user_id 正常取值", S.event_user_id(fake_event("NormalEvent", user_id="7")), "7")
check("D10 session.event_user_id 抛错时给空串", S.event_user_id(_u), "")

# ========== F. 真实适配器事件（非 OneBot，用真包的事件模型） ==========

print("\n=== F. 真实适配器事件（Mirai） ===")
from nonebot.adapters.mirai.event import MemberJoinEvent as MiraiJoin, MemberLeaveEventQuit as MiraiQuit  # noqa: E402
from nonebot.adapters.mirai.model.relationship import (  # noqa: E402
    Group as MiraiGroup, Member as MiraiMember, MemberPerm,
)

_mgroup = MiraiGroup.model_construct(id=11001, name="群", account_perm=MemberPerm.Member)
_mmember = MiraiMember.model_construct(id=20002, name="李四", permission=MemberPerm.Member, group=_mgroup)
_mirai_join = MiraiJoin.model_construct(group=_mgroup, member=_mmember)
_mirai_quit = MiraiQuit.model_construct(group=_mgroup, member=_mmember)
MIRAI_INFO = SessionInfo(key="mirai:11001", slug="mirai", scene_path="11001",
                         native_chat_id="11001", adapter_name="Mirai")
check("F1 真事件类型闸门（内核 on_notice 依据）", _mirai_join.get_type(), "notice")
check("F2 rule 预筛", N.is_group_notice(_mirai_join), True)
check("F3 真 Mirai 加入事件", run(N.notice_text(FAKE_BOT, MIRAI_INFO, _mirai_join)), "李四 加入了群聊")
check("F4 真 Mirai 退群事件", run(N.notice_text(FAKE_BOT, MIRAI_INFO, _mirai_quit)), "李四 离开了群聊")
check("F5 真 Mirai 事件不泄露 JSON dump", "{" not in run(N.notice_text(FAKE_BOT, MIRAI_INFO, _mirai_join)), True)
N.display_user = _real_display_user

# ========== E. handler 集成 ==========

print("\n=== E. handler 集成 ===")
mod._group_chunks.pop("onebot11:617770183", None)
run(mod.handle_group_notice(bot, increase()))
buf = mod._group_chunks.get("onebot11:617770183", [])
check("E1 入缓冲条数", len(buf), 1)
check("E2 来源标为系统", buf[0].user_name, "系统")
check("E3 系统消息无 user_id", buf[0].user_id, "")
check("E4 内容", buf[0].content, "李四 加入了群聊")

mod._group_chunks.pop("onebot11:999", None)
run(mod.handle_group_notice(bot, increase(group_id=999)))
check("E5 未启用群不入缓冲", mod._group_chunks.get("onebot11:999"), None)

mod._group_chunks.pop("onebot11:20001", None)
run(mod.handle_group_notice(bot, poke(user_id=20001, target_id=10000, group_id=None)))
check("E6 私聊通知不入缓冲", mod._group_chunks.get("onebot11:20001"), None)

mod._group_chunks.pop("console:console-chat", None)
_console_ev = fake_event("ConsoleNoticeEvent", "notice", user_id="")
run(mod.handle_group_notice(FAKE_BOT, _console_ev))
check("E7 非 OneBot 且会话解析不出 → 跳过（不炸）", mod._group_chunks.get("console:console-chat"), None)

# 通知不该污染调用身份
mod._group_chunks.pop("onebot11:617770183", None)
run(mod.handle_group_notice(bot, ban()))
check("E8 不设置 _current_user_id（保持原值）", getattr(mod, "_current_user_id", None), None)

print(f"\n=== 结果: {PASSED} 通过 / {len(FAILED)} 失败 ===")
if FAILED:
    print("失败项: " + ", ".join(FAILED))
sys.exit(1 if FAILED else 0)
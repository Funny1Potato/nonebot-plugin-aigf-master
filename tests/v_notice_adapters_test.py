"""跨适配器「群通知」实测：为每个适配器构造真实通知事件，跑

    get_type() == "notice"  →  notice.is_group_notice()  →  resolve_session()  →  notice_text()
    →  handle_group_notice() 入缓冲

通知事件的构造配方在 v_notice_recipes.py（同一目录，由独立步骤生成与自验证）。
"""
from _bootstrap import REPO, SOURCE, TMP, close_loop


import asyncio
import importlib
import inspect
import json
import os
import shutil
import sys
from pathlib import Path

_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)
close_loop(_LOOP)

if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
os.chdir(TMP)

import nonebot  # noqa: E402

nonebot.init(
    driver="~none", localstore_use_cwd=True, log_level="CRITICAL", uninfo_cache=False,
    command_start={""}, superusers={"945126014"},
    aigfm_llm_api_key="test-key", aigfm_llm_base_url="http://127.0.0.1:9/v1",
    aigfm_vlm_enabled=False, aigfm_anime_backend="off",
    aigfm_enabled_groups=[],
    aigfm_enabled_private=[],
    aigfm_batch_timeout=0.2, aigfm_batch_count=100,
)
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot.adapters import Bot as NBBot  # noqa: E402
from nonebot_plugin_aigf_master import notice as N  # noqa: E402
from nonebot_plugin_aigf_master import session as S  # noqa: E402

from nonebot import logger as _nb_logger  # noqa: E402


def _sink(message):
    text = message.record["message"]
    if text.startswith("[会话]"):
        sys.stdout.write("      LOG " + text[:200] + "\n")


_nb_logger.add(_sink, level="DEBUG", format="{message}")

try:
    from v_notice_recipes import RECIPES as NOTICE_RECIPES  # noqa: E402

    try:
        from v_notice_recipes import RULE_REJECTED  # noqa: E402
    except Exception:
        RULE_REJECTED = {}
except Exception as e:  # pragma: no cover
    NOTICE_RECIPES, RULE_REJECTED = {}, {}
    print(f"[warn] 通知事件配方不可用: {type(e).__name__}: {e}")

# 预筛历史上恒为 False 的那批（会话信息在嵌套字段里）：现在也应该被规则接受；
# 若规则仍不收，就当「该适配器事件没有群/频道字段（会话按私聊处理）」而不是失败
SUSPECT = set(RULE_REJECTED)
ALL_RECIPES = {**RULE_REJECTED, **NOTICE_RECIPES}

# Console / Mail 适配器本身没有通知事件类型
NO_NOTICE = {"Console", "Mail"}


def run(coro):
    return _LOOP.run_until_complete(coro)


# ==================== bot mock（与 v_ma_adapters_test.py 同套路） ====================

class _Stub:
    def __getattr__(self, name):
        return self

    def __str__(self):
        return "stub"


class _ActionFailed(Exception):
    pass


def _action_failed():
    try:
        from nonebot.exception import ActionFailed
        return ActionFailed(None)
    except Exception:
        return _ActionFailed()


class MockAdapter:
    def __init__(self, name, message_class):
        self._name = name
        self._message_class = message_class
        self.config = nonebot.get_driver().config

    def get_name(self):
        return self._name

    def get_message_class(self):
        return self._message_class

    async def _call_api(self, bot, api, **data):
        bot.calls.append(("call_adapter_api:" + api, (), data))
        return _Stub()


def make_bot(display_name, bot_cls, message_class, extras=None, api=None, fail_api=()):
    """真 Bot 类的子类，绕过 __init__；所有公开方法用实例级 recorder 覆盖（返回 _Stub）"""

    class RecorderBot(bot_cls):
        def __init__(self):
            pass

        def __getattr__(self, name):
            if name.startswith("__") or name in ("adapter", "self_id", "calls", "_api", "_fail"):
                raise AttributeError(name)

            async def _rec(*a, **kw):
                self.calls.append((name, a, kw))
                if name in self._fail:
                    raise _action_failed()
                if name in self._api:
                    return self._api[name]
                return _Stub()
            return _rec

        async def call_api(self, api, **data):
            if "call_api" in self._fail:
                raise _action_failed()
            self.calls.append(("call_api:" + str(api), (), data))
            return _Stub()

    bot = RecorderBot()
    bot.adapter = MockAdapter(display_name, message_class)
    bot.self_id = "10000"
    bot.calls = []
    bot._api = api or {}
    bot._fail = set(fail_api)

    def _recorder(name):
        async def _call(*a, **kw):
            bot.calls.append((name, a, kw))
            if name in bot._fail:
                raise _action_failed()
            if name in bot._api:
                value = bot._api[name]
                if isinstance(value, Exception):
                    raise value
                return value
            return _Stub()
        return _call

    for name in dir(bot_cls):
        if name.startswith("_") or name in ("call_api",):
            continue
        try:
            attr = inspect.getattr_static(bot_cls, name)
        except Exception:
            continue
        if isinstance(attr, (property, classmethod, staticmethod)):
            continue
        func = getattr(bot_cls, name, None)
        if inspect.iscoroutinefunction(func):
            setattr(bot, name, _recorder(name))
    if display_name == "Satori":
        bot.get_self_id = lambda: bot.self_id
    for key, value in (extras or {}).items():
        object.__setattr__(bot, key, value)
    return bot


# ==================== 各适配器的 bot 配置（与消息套件同源） ====================

CASES = [
    dict(name="OneBot V11", module="nonebot.adapters.onebot.v11",
         api={"get_group_info": {"group_id": 11001, "group_name": "群"},
              "get_group_member_info": {"user_id": 20002, "nickname": "李四", "card": "名片李",
                                        "role": "member", "sex": "male", "join_time": 1},
              "get_group_member_list": [{"user_id": 20002, "nickname": "李四", "card": "名片李"}]}),
    dict(name="OneBot V12", module="nonebot.adapters.onebot.v12", extras={"platform": "qq"},
         api={"get_group_info": {"group_id": "11001", "group_name": "群"},
              "get_user_info": {"user_id": "20002", "user_name": "李四"},
              "get_group_member_info": {"user_id": "20002", "user_name": "李四"},
              "get_group_member_list": [{"user_id": "20002", "user_name": "李四"}]}),
    dict(name="Satori", module="nonebot.adapters.satori",
         extras={"platform": "qq", "_self_info": type("I", (), {"features": []})()}),
    dict(name="Telegram", module="nonebot.adapters.telegram",
         fail_api=("get_chat_member", "get_user_profile_photos")),
    dict(name="Discord", module="nonebot.adapters.discord",
         api={"get_guild": type("G", (), {"name": "公会", "icon": ""})(),
              "get_channel": type("C", (), {"id": 2, "name": "频道", "type": 0, "guild_id": 10})(),
              "get_guild_member": type("M", (), {"nick": "李四", "roles": [], "joined_at": 0})(),
              "get_guild_roles": []}),
    dict(name="QQ", module="nonebot.adapters.qq",
         extras={"bot_info": type("B", (), {"id": "10000"})()},
         fail_api=("get_guild", "get_channel")),
    dict(name="Feishu", module="nonebot.adapters.feishu", fail_api=("call_api",)),
    dict(name="Milky", module="nonebot.adapters.milky"),
    dict(name="Mirai", module="nonebot.adapters.mirai", fail_api=("get_member_profile",)),
    dict(name="Kaiheila", module="nonebot.adapters.kaiheila",
         api={"user_view": type("U", (), {"id_": "20002", "username": "李四", "nickname": "李四",
                                          "avatar": "", "roles": [], "joined_at": 0})(),
              "channel_view": type("C", (), {"id_": "ch-1", "name": "频道", "guild_id": "g-1", "is_category": False, "type": 1})(),
              "guild_view": type("G", (), {"id_": "g-1", "name": "公会", "icon": ""})(),
              "guildRole_list": []}),
    dict(name="DoDo", module="nonebot.adapters.dodo",
         api={"get_island_info": {"island_source_id": "isl-1", "island_name": "岛"},
              "get_channel_info": {"channel_id": "ch-1", "channel_name": "频道"},
              "get_member_role_list": []}),
    dict(name="Kritor", module="nonebot.adapters.kritor",
         fail_api=("get_group_info", "get_group_member_info")),
    dict(name="Minecraft", module="nonebot.adapters.minecraft"),
    dict(name="WXMP", module="nonebot.adapters.wxmp"),
    dict(name="EFChat", module="nonebot.adapters.efchat",
         extras={"cfg": type("Cfg", (), {"channel": "room-1"})()}),
    dict(name="YunHu", module="nonebot.adapters.yunhu", fail_api=("get_group_info",)),
    dict(name="bilibili Live", module="nonebot.adapters.bilibili_live", bot="WebBot"),
]

RESULTS = {}


def record(adapter, feature, value):
    RESULTS.setdefault(adapter, {})[feature] = str(value)[:200]


async def check_notice(case, bot):
    name = case["name"]
    if name in NO_NOTICE:
        return "N/A 适配器无通知事件类型"
    if name not in ALL_RECIPES:
        return "N/A 未构造通知事件"
    try:
        event = ALL_RECIPES[name]()
    except Exception as e:
        return f"FAIL 配方抛错 {type(e).__name__}: {str(e)[:100]}"

    try:
        etype = event.get_type()
    except Exception as e:
        return f"FAIL get_type 抛错 {type(e).__name__}: {str(e)[:80]}"
    if etype != "notice":
        return f"FAIL 类型闸门 get_type={etype!r}"
    if not N.is_group_notice(event):
        if name in SUSPECT:
            return "N/A 事件无群/频道字段（会话按私聊处理，按预期跳过）"
        return "FAIL 规则预筛未通过（无会话线索）"

    info = await S.resolve_session(bot, event)
    if info is None:
        try:
            from nonebot_plugin_alconna import get_target
            target = get_target(event, bot)
            detail = f"Target 可解析={target}"
        except Exception as e:
            detail = f"Target 也失败: {type(e).__name__}: {str(e)[:60]}"
        return f"FAIL 通知的会话解析失败（{detail}）"
    if info.is_private:
        return f"FAIL 被判成私聊 key={info.key}"

    expected_keys = {"Feishu": "feishu:oc-1", "Kritor": "kritor:11001",
                     "bilibili Live": "bilibililive:12345", "EFChat": "efchat:room-1"}
    if name in expected_keys and info.key != expected_keys[name]:
        return f"FAIL notice does not match message session: {info.key}"

    text = await N.notice_text(bot, info, event)
    if not text:
        return "FAIL 文案为空（handler 会跳过）"
    if "{" in text or "[{" in text:
        return f"FAIL 疑似 JSON dump 进 prompt: {text[:80]!r}"

    # 会话启用清单与本用例无关：临时放行 is_enabled，专测通知链路是否入缓冲
    real_enabled = mod.is_enabled
    mod.is_enabled = lambda i, c: True
    mod._group_chunks.pop(info.key, None)
    try:
        await mod.handle_group_notice(bot, event)
    finally:
        mod.is_enabled = real_enabled
    buf = mod._group_chunks.get(info.key, [])
    if not buf or buf[-1].user_name != "系统":
        return f"FAIL 未入缓冲 text={text!r}"

    label = "OK"
    if text == event.get_event_name():
        label = "OK(仅事件名)"
    return f"{label} key={info.key} text={text!r}"


async def main():
    for case in CASES:
        name = case["name"]
        print(f"\n=== {name}")
        try:
            module = importlib.import_module(case["module"])
            bot_cls = getattr(module, case["bot"]) if "bot" in case else getattr(module, "Bot")
            message_cls = getattr(module, "Message")
        except Exception as e:
            record(name, "群通知", f"FAIL 装载 {type(e).__name__}: {str(e)[:100]}")
            print(f"  装载 FAIL {type(e).__name__}: {str(e)[:160]}")
            continue
        bot = make_bot(name, bot_cls, message_cls, case.get("extras"), case.get("api"),
                       case.get("fail_api", ()))
        try:
            value = await check_notice(case, bot)
        except Exception as e:
            value = f"FAIL 抛错 {type(e).__name__}: {str(e)[:120]}"
        record(name, "群通知", value)
        print(f"  群通知     {value[:170]}")


run(main())

(TMP / "notice_matrix.json").write_text(
    json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")

print("\n\n===== 群通知矩阵")
print("适配器".ljust(14) + "结果")
for case in CASES:
    v = RESULTS.get(case["name"], {}).get("群通知", "-")
    status = "OK" if v.startswith("OK") else ("N/A" if v.startswith("N/A") else "FAIL")
    print(case["name"].ljust(14) + status.ljust(6) + v[:120])

bad = {a: d["群通知"] for a, d in RESULTS.items() if not d["群通知"].startswith(("OK", "N/A"))}
print(f"\n检查点 {sum(len(d) for d in RESULTS.values())} 个，失败 {len(bad)}")
for a, v in bad.items():
    print(f"  - {a}: {v[:150]}")
sys.exit(1 if bad else 0)

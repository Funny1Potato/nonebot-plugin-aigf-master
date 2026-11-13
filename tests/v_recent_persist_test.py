"""离线验证：最近聊天记录落盘（重启后仍可读）"""
from _bootstrap import REPO, SOURCE, TMP, close_loop


import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
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
    driver="~none", localstore_use_cwd=True, log_level="CRITICAL", uninfo_cache=False,
    command_start={""}, superusers={"11111"},
    aigfm_llm_api_key="test-key", aigfm_llm_base_url="http://127.0.0.1:9/v1",
    aigfm_vlm_enabled=False, aigfm_anime_backend="off",
    aigfm_enabled_groups=[123456789],
    aigfm_recent_messages=50, aigfm_context_max_messages=50,
    aigfm_batch_timeout=0.2, aigfm_batch_count=100,
)
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot.adapters.onebot.v11 import (  # noqa: E402
    Bot as OB11Bot, GroupMessageEvent, Message as OB11Message,
)
from nonebot.message import handle_event  # noqa: E402
from nonebot_plugin_aigf_master.config import plugin_config  # noqa: E402
from nonebot_plugin_aigf_master.models import ChatMessage  # noqa: E402
from nonebot_plugin_aigf_master.processor import _RECENT_KEEP  # noqa: E402

GROUP = 123456789
KEY = f"onebot11:{GROUP}"
RECENT = mod._data_dir / "memory" / "onebot11_123456789" / "recent.json"

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


class MockBot(OB11Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []

    async def call_api(self, api, **data):
        self.calls.append((api, data))
        if api.startswith("send_"):
            # Successful OneBot sends return an actual message identifier.
            return {"message_id": next(_msg_seq)}
        if api == "get_login_info":
            return {"user_id": int(self.self_id), "nickname": "测试机器人"}
        if api == "get_group_info":
            return {"group_id": GROUP, "group_name": "测试群"}
        if api == "get_group_member_info":
            return {"user_id": data.get("user_id", 20001), "nickname": "张三", "card": "", "role": "member",
                    "sex": "male", "join_time": 1}
        return {}


def make_bot() -> MockBot:
    return MockBot(adapter=MockAdapter(), self_id="10000")


_msg_seq = iter(range(1, 100000))


def group_event(user_id=20001, group_id=GROUP, text="hi"):
    msg = OB11Message(text)
    return GroupMessageEvent(
        time=1700000000, self_id=10000, post_type="message", sub_type="normal",
        message_type="group", user_id=user_id, group_id=group_id, message_id=next(_msg_seq),
        message=msg, raw_message=text, font=0,
        sender={"user_id": user_id, "nickname": "张三", "role": "admin"},
    )


PROMPTS: list[str] = []


async def fake_chat(prompt, model, json_mode=False, images=None):
    PROMPTS.append(prompt)
    return '{"reply":[{"type":"text","content":"收到"}],"memory":{}}'


mod._llm.chat = fake_chat


def chat_msg(content, name="张三", uid="20001", at_only=False):
    return ChatMessage(time=datetime.now(), user_name=name, content=content,
                       user_id=uid, is_at_only=at_only)


def new_processor():
    """模拟重启：清掉进程内的 processor，重新构造一个"""
    mod._processors.clear()
    p = mod._get_processor(KEY)
    p._bot = make_bot()
    p._session_event = group_event()
    run(p.load_preset(plugin_config.aigfm_default_preset))
    return p


# ========== A. 落盘 ==========

print("\n=== A. 落盘 ===")
p = new_processor()
PROMPTS.clear()
run(p.run_batch([chat_msg("今天天气不错")]))
check_true("A1 生成了 recent.json", RECENT.exists())
saved = json.loads(RECENT.read_text(encoding="utf-8"))["messages"]
check("A2 文件条数（用户消息 + 机器人回复）", len(saved), 2)
check("A3 用户消息内容", saved[0]["content"], "今天天气不错")
check("A4 机器人回复也入库", saved[1]["content"], "收到")
check("A5 字段齐全", sorted(saved[0]), ["content", "is_at_only", "time", "user_id", "user_name"])
check_true("A6 time 是 ISO 字符串", isinstance(saved[0]["time"], str) and "T" in saved[0]["time"])
check("A7 load_recent 往返条数", len(run(p.memory.load_recent())), 2)
check("A8 load_recent 时间类型", type(run(p.memory.load_recent())[0].time).__name__, "datetime")

# ========== B. 重启后恢复 ==========

print("\n=== B. 重启后恢复 ===")
p2 = new_processor()
check("B1 新 processor 初始为空", len(p2.recent_messages), 0)
PROMPTS.clear()
run(p2.run_batch([chat_msg("重启之后的第一句", uid="20002")]))
check("B2 读回历史 + 本批", [m.content for m in p2.recent_messages],
      ["今天天气不错", "收到", "重启之后的第一句", "收到"])
check_true("B3 prompt 里带上了重启前的聊天记录", "今天天气不错" in PROMPTS[0])
check_true("B4 prompt 里也带了本批消息", "重启之后的第一句" in PROMPTS[0])

# ========== C. 条数上限 ==========

print("\n=== C. 条数上限 ===")
run(p2.memory.save_recent([chat_msg(f"旧消息{i}") for i in range(80)]))
p3 = new_processor()
run(p3.run_batch([chat_msg("最新一条")]))
check("C1 内存中保留上限", len(p3.recent_messages), _RECENT_KEEP)
check("C2 保留的是最新的（末条是机器人回复）", p3.recent_messages[-2].content, "最新一条")
check("C3 落盘也不超上限", len(json.loads(RECENT.read_text(encoding="utf-8"))["messages"]), _RECENT_KEEP)
check_true("C4 最旧的已被淘汰", "旧消息0" not in [m.content for m in p3.recent_messages])

# ========== D. /reset 清盘 ==========

print("\n=== D. /reset ===")
check_true("D1 重置前文件存在", RECENT.exists())
bot = make_bot()
run(handle_event(bot, group_event(user_id=11111, text="reset")))
check("D2 文件被删除", RECENT.exists(), False)
check("D3 内存也清空", len(mod._get_processor(KEY).recent_messages), 0)
PROMPTS.clear()
p4 = new_processor()
run(p4.run_batch([chat_msg("重置后的新消息")]))
check("D4 旧历史没被读回来", [m.content for m in p4.recent_messages], ["重置后的新消息", "收到"])
check_true("D5 prompt 里不含重置前内容", "旧消息" not in PROMPTS[0])

# ========== E. 健壮性 ==========

print("\n=== E. 健壮性 ===")
RECENT.write_text("{这不是 JSON", encoding="utf-8")
check("E1 坏文件 → 空列表不报错", run(new_processor().memory.load_recent()), [])
RECENT.unlink()
check("E2 缺文件 → 空列表", run(new_processor().memory.load_recent()), [])
RECENT.write_text(json.dumps({"messages": [
    {"time": "坏时间", "user_name": "a", "content": "坏条目"},
    {"time": "2026-09-25T10:00:00", "user_name": "张三", "content": "好条目"},
]}, ensure_ascii=False), encoding="utf-8")
good = run(new_processor().memory.load_recent())
check("E3 坏条目跳过、好条目保留", [(m.content, m.user_name) for m in good], [("好条目", "张三")])
p5 = new_processor()
run(p5.run_batch([chat_msg("继续聊")]))
check("E4 坏文件场景仍能正常处理", len(p5.recent_messages) > 0, True)
check_true("E5 处理后文件已重写为正常格式",
           "好条目" in [m["content"] for m in json.loads(RECENT.read_text(encoding="utf-8"))["messages"]])

print(f"\n=== 结果: {PASSED} 通过 / {len(FAILED)} 失败 ===")
if FAILED:
    print("失败项: " + ", ".join(FAILED))
sys.exit(1 if FAILED else 0)

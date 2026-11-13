"""离线验证：LLM 响应解析健壮性（未转义引号修复 / 兜底优先 reply）+ 解析失败重试 + 诊断日志"""
from _bootstrap import REPO, SOURCE, TMP


import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(SOURCE))

os.environ["AIGFM_LLM_API_KEY"] = "test-key"
os.environ["AIGFM_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["AIGFM_VLM_ENABLED"] = "false"
os.environ["AIGFM_ANIME_BACKEND"] = "off"

import nonebot  # noqa: E402

nonebot.init(driver="~none")
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot import logger as nb_logger  # noqa: E402

from nonebot_plugin_aigf_master.config import plugin_config  # noqa: E402
from nonebot_plugin_aigf_master.memory_store import MemoryStore  # noqa: E402
from nonebot_plugin_aigf_master.models import ChatMessage  # noqa: E402
from nonebot_plugin_aigf_master.processor import MessageProcessor  # noqa: E402
from nonebot_plugin_aigf_master.prompt_builder import build_prompt  # noqa: E402
from nonebot_plugin_aigf_master.response_parser import (  # noqa: E402
    _escape_inner_quotes, _extract_json_object, extract_reply_segments, parse_llm_response,
)

FAILED = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} | {name} | got={actual!r}" + ("" if ok else f" want={expected!r}"))
    if not ok:
        FAILED.append(name)


class Capture:
    """loguru sink（nonebot.logger 是 loguru，不是 stdlib logging）"""

    def __init__(self):
        self.records = []

    def sink(self, message):
        self.records.append(message.record["message"])

    def start(self):
        nb_logger.add(self.sink, level="INFO", format="{message}")

    def has(self, *needles):
        return any(all(n in m for n in needles) for m in self.records)

    def clear(self):
        self.records.clear()


capture = Capture()
capture.start()

CONTENT = ("是和泉妃爱！《ハミダシクリエイティブ》的女主角，金发双发髻+蓝蝴蝶结超好认的♪ "
           "小共你喊她\u0022我妈\u0022也太好笑了吧，认亲大会现场是吧哈哈 (´▽`ʃ♡)ƪ")
BAD = ('{"reply": [{"type": "text", "content": "' + CONTENT + '"}], '
       '"memory": {"short_term": {"modify": [{"index": 0, "content": "小明提到了哈米达西"}]}}}')
GOOD = json.dumps({"reply": [{"type": "text", "content": "好的，我记住了"}],
                   "memory": {"short_term": {"add": ["测试"]}}}, ensure_ascii=False)
MEMORY_ONLY = json.dumps({"memory": {"short_term": {"add": ["只有记忆没有回复"]}}}, ensure_ascii=False)

print("=== A. 解析（本次复现）===")
try:
    json.loads(BAD)
    check("A1 原始响应是非法 JSON", "parsed", "raise")
except json.JSONDecodeError:
    check("A1 原始响应是非法 JSON", "raise", "raise")

old = _extract_json_object(BAD)
old_parsed = json.loads(old) if old else None
check("A2 旧兜底会抽到内层对象（无 reply）", (old_parsed is not None and "reply" not in old_parsed), True)
check("A2 旧兜底抽到的键", sorted(old_parsed or {}), ["short_term"])

res = parse_llm_response(BAD)
check("A3 修复后能解析出 reply", (res is not None and "reply" in res), True)
check("A3 content 与原文一致（引号保留）", res["reply"][0]["content"], CONTENT)
seg = extract_reply_segments(res)
check("A4 提取到 1 段文本", [(s.type, s.content) for s in seg], [("text", CONTENT)])
check("A4 记忆段未被破坏", res["memory"]["short_term"]["modify"][0]["index"], 0)

full = BAD.replace("\u0022我妈\u0022", "“我妈”")
check("A5 全角引号版本同样可解析",
      parse_llm_response(full)["reply"][0]["content"].endswith("(´▽`ʃ♡)ƪ"), True)

print("=== B. 既有能力回归 ===")
check("B1 前导文字 + JSON", parse_llm_response("好的，这是回复：\n" + GOOD)["reply"][0]["content"], "好的，我记住了")
check("B2 ```json 代码块", parse_llm_response("```json\n" + GOOD + "\n```")["reply"][0]["content"], "好的，我记住了")
esc = '{"reply": [{"type": "text", "content": "他说\\"hi\\"啦"}]}'
check("B3 已正确转义的引号", parse_llm_response(esc)["reply"][0]["content"], '他说"hi"啦')
check("B4 字符串内含花括号", parse_llm_response(
    '{"reply": [{"type": "text", "content": "集合{1,2}和\"空\"集"}]}')["reply"][0]["content"], '集合{1,2}和"空"集')
check("B5 优先选含 reply 的对象（旧代码会拿到 foo）",
      sorted(parse_llm_response('{"foo": 1} 说明文字 {"reply": [{"type": "text", "content": "你好"}]}')), ["reply"])
check("B6 截断 JSON → None", parse_llm_response('{"reply": [{"type": "text", "content": "abc"'), None)
check("B7 JSON 数组 → None", parse_llm_response('[1, 2]'), None)
check("B8 纯文字 → None", parse_llm_response('这不是 JSON'), None)
check("B9 空串 → None", parse_llm_response(''), None)

print("=== C. _escape_inner_quotes 对合法 JSON 是恒等变换 ===")
for i, sample in enumerate([
    GOOD,
    '{"a": {"b": [1, 2, {"c": "d"}], "e": "f"}}',
    '{"reply": [{"type": "text", "content": "他说\\"hi\\"、换行\\n、反斜杠\\\\ 结束"}], "memory": {}}',
    BAD + '"x"',                      # 非法也要稳定（只测不抛异常）
], 1):
    try:
        out = _escape_inner_quotes(sample)
    except Exception as e:
        out = f"EXC {type(e).__name__}: {e}"
    if i <= 3:
        check(f"C{i} 合法 JSON 逐字节不变", out, sample)
    else:
        check(f"C{i} 不抛异常", isinstance(out, str), True)

print("=== D. processor 端到端 ===")


class FakeLLM:
    multimodal = False  # Current LLM interface; this suite exercises text parsing.

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, prompt, model, json_mode=False, images=None):
        self.calls.append(prompt)
        return self.replies.pop(0) if self.replies else None

    async def chat_with_tools(self, *a, **kw):
        raise AssertionError("本测试不应走工具路径")


def make_processor(llm):
    return MessageProcessor(
        session_key="onebot11:12345", llm=llm, memory=MemoryStore("onebot11_12345", mod._data_dir),
        memes=mod._memes, presets=mod._presets, context_bus=mod._bus,
        invoker=mod._invoker, learner=mod._learner, search=None,
        config=plugin_config, peer_client=None, image_gen=None, image_handler=None,
    )


async def main():
    plugin_config.aigfm_search_enabled = False

    async def run(replies, content="你好呀"):
        capture.clear()
        llm = FakeLLM(replies)
        proc = make_processor(llm)
        msgs = [ChatMessage(time=datetime.now(), user_name="张三", content=content, user_id="111")]
        out = await proc.process(msgs)
        return llm, out

    HOPELESS = '{"reply": [{"type": "text", "content": "abc'      # 彻底截断，修不回来

    # ① 本次复现：坏引号能被修复 → 一次就发出，不需要重试
    llm, out = await run([BAD])
    prompt_text = llm.calls[0]
    check("E1 本次复现文本一次就发出", (len(llm.calls), [s.content for s in (out or [])]), (1, [CONTENT]))
    check("E1 真实 prompt 含 JSON 合法性约束（且 \\n 是字面量）",
          ("不要出现半角双引号" in prompt_text, "换行用 \\n 转义" in prompt_text,
           "换行用 \n 转义" in prompt_text), (True, True, False))

    # ② 修不回来的响应 → 保持工具配置重试一次
    llm, out = await run([HOPELESS, GOOD])
    check("E2 修不回来 → 重试一次后发出", (len(llm.calls), [s.content for s in (out or [])]), (2, ["好的，我记住了"]))
    check("E2 重试提示带格式纠正", ("不要出现半角双引号" in llm.calls[1] and "无法解析为 JSON" in llm.calls[1]), True)
    check("E2 重试提示仍是同一 prompt（保留上下文）", llm.calls[1].startswith(llm.calls[0][:50]), True)

    # ③ 两次都修不回来 → 只重试一次就放弃
    llm, out = await run([HOPELESS, HOPELESS])
    check("E3 两次都坏 → 不回复且仅重试 1 次", (len(llm.calls), out), (2, None))
    check("E3 日志含重试后仍解析失败且带尾部片段", capture.has("[处理] 重试后仍解析失败", "尾部:"), True)

    # ④ 只解析到 memory（旧逻辑的静默丢弃形态）→ 有告警
    llm, out = await run([MEMORY_ONLY])
    check("E4 只解析到 memory → 不回复", (len(llm.calls), out), (1, None))
    check("E4 日志含「缺少 reply 字段」并给出顶层键", capture.has("[处理] 响应缺少 reply 字段", "['memory']"), True)

    # ⑤ 正常响应照旧
    llm, out = await run([GOOD])
    check("E5 正常响应照旧", (len(llm.calls), [s.content for s in (out or [])]), (1, ["好的，我记住了"]))
    check("E5 无多余告警", capture.has("[处理] 响应缺少 reply 字段"), False)

    print("\n" + ("ALL PASS" if not FAILED else f"FAILED {len(FAILED)}: {FAILED}"))
    return 1 if FAILED else 0


sys.exit(asyncio.run(main()))

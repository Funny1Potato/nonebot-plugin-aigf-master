"""离线验证：DeepSeek 搜索后端（请求构造 / 结果解析 / 错误处理）"""
from _bootstrap import REPO, SOURCE, TMP, close_loop


import asyncio
import sys

_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)
close_loop(_LOOP)


def run(coro):
    return _LOOP.run_until_complete(coro)


import nonebot  # noqa: E402

nonebot.init(driver="~none", localstore_use_cwd=True, log_level="CRITICAL")

import importlib.util  # noqa: E402

# 默认测当前仓库；传参可测任意 search_client.py
_target = sys.argv[1] if len(sys.argv) > 1 else (
    str(SOURCE / "nonebot_plugin_aigf_master" / "search_client.py"))
_spec = importlib.util.spec_from_file_location("aigf_search_client", _target)
SC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SC)
print(f"被测文件: {_target}")
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


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class FakeClient:
    """替换 httpx.AsyncClient，记录请求并返回预设响应"""

    last = None
    response = FakeResp()

    def __init__(self, *a, **kw):
        FakeClient.last = {"init": (a, kw)}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, **kw):
        FakeClient.last = {"url": url, "headers": headers or {}, "json": json or {}, "kw": kw}
        resp = FakeClient.response
        if isinstance(resp, Exception):
            raise resp
        return resp


SC.httpx.AsyncClient = FakeClient

print("\n=== 工厂 ===")
check("F1 deepseek 有 Key → 建客户端", type(SC.create_search_client("deepseek", "sk-xxx")).__name__,
      "DeepSeekSearchClient")
check("F2 deepseek 无 Key → None", SC.create_search_client("deepseek", ""), None)
check("F3 其它后端不受影响", type(SC.create_search_client("tavily", "k")).__name__, "TavilySearchClient")
check("F4 openwebsearch 仍按 base_url 建", type(SC.create_search_client("openwebsearch", "", "http://x")).__name__,
      "OpenWebSearchClient")
check("F5 未知后端 → None", SC.create_search_client("nope", "k"), None)

print("\n=== 请求构造 ===")
FakeClient.response = FakeResp(200, {"results": []})
client = SC.DeepSeekSearchClient("sk-test")
run(client.search("今天天气", 3))
req = FakeClient.last
check("Q1 URL", req["url"], "https://api.deepseek.com/v1/tools/search")
check("Q2 鉴权头", req["headers"].get("Authorization"), "Bearer sk-test")
check("Q3 query", req["json"].get("query"), "今天天气")
check("Q4 count", req["json"].get("count"), 3)
check("Q5 默认不带 need_content", "need_content" in req["json"], False)
run(client.search("x", 100))
check("Q6 count 上限 50", FakeClient.last["json"].get("count"), 50)
run(client.search("x", 0))
check("Q7 count 下限 1", FakeClient.last["json"].get("count"), 1)
custom = SC.DeepSeekSearchClient("sk-test", "https://proxy.example.com/")
run(custom.search("x", 3))
check("Q8 自定义根地址", FakeClient.last["url"], "https://proxy.example.com/v1/tools/search")

print("\n=== 结果解析 ===")
FakeClient.response = FakeResp(200, {"results": [
    {"title": "标题A", "url": "https://a", "snippet": "短摘要", "summary": "长摘要给模型", "content": ""},
    {"title": "标题B", "url": "https://b", "snippet": "只有短摘要", "summary": ""},
    {"title": "标题C", "url": "https://c", "snippet": "", "summary": ""},
]})
res = run(client.search("x", 3))
check("R1 条数", len(res), 3)
check("R2 优先用 summary", res[0]["content"], "长摘要给模型")
check("R3 回落到 snippet", res[1]["content"], "只有短摘要")
check("R4 都没有则为空", res[2]["content"], "")
check("R5 title", res[0]["title"], "标题A")
check("R6 url", res[0]["url"], "https://a")
FakeClient.response = FakeResp(200, {"results": []})
check("R7 无结果 → 空列表", run(client.search("x", 3)), [])

print("\n=== 错误处理 ===")
for code, name in [(401, "Key 无效"), (402, "余额不足"), (429, "限流"), (500, "服务端错误")]:
    FakeClient.response = FakeResp(code, {"error": {"message": f"boom {code}", "type": "invalid_request_error",
                                                     "param": None, "code": "invalid_request_error"}})
    check(f"E{code} 返回空列表不抛异常", run(client.search("x", 3)), [])
FakeClient.response = FakeResp(400, {"error": {"message": "'count' must be between 1 and 50"}})
check("E400 空列表", run(client.search("x", 3)), [])
FakeClient.response = FakeResp(500, "not json")
check("E500 错误体非 JSON 也不炸", run(client.search("x", 3)), [])
FakeClient.response = RuntimeError("连接超时")
check("E 网络异常不炸", run(client.search("x", 3)), [])

print("\n=== 格式化输出 ===")
FakeClient.response = FakeResp(200, {"results": [
    {"title": "标题A", "url": "https://a", "summary": "摘要A"},
]})
res = run(client.search("x", 3))
text = client.format_results(res)
check("F 标题在文本里", "标题A" in text, True)
check("F 摘要在文本里", "摘要A" in text, True)
check("F 来源链接在文本里", "https://a" in text, True)
check("F 空结果提示", client.format_results([]), "未找到相关搜索结果")

print(f"\n=== 结果: {PASSED} 通过 / {len(FAILED)} 失败 ===")
if FAILED:
    print("失败项: " + ", ".join(FAILED))
sys.exit(1 if FAILED else 0)
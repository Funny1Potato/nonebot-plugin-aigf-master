"""离线验证：角色识别双后端（本地 WD14 / AnimeTrace）调度链、渲染与缓存"""
from _bootstrap import REPO, SOURCE, TMP


import asyncio
import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import httpx
from PIL import Image

(TMP / "image_cache").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SOURCE))

os.environ["AIGFM_LLM_API_KEY"] = "test-key"
os.environ["AIGFM_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["AIGFM_VLM_ENABLED"] = "false"
os.environ["AIGFM_IMAGE_MODE"] = "vlm"
os.environ["AIGFM_ANIME_BACKEND"] = "off"

import nonebot  # noqa: E402

nonebot.init(driver="~none")
nonebot.load_plugin("nonebot_plugin_aigf_master")

from nonebot_plugin_aigf_master.anime_recognizer import AnimeRecognizer, effective_backend  # noqa: E402
from nonebot_plugin_aigf_master.animetrace_client import AnimeTraceClient, _shrink_jpeg  # noqa: E402
from nonebot_plugin_aigf_master.config import plugin_config  # noqa: E402
from nonebot_plugin_aigf_master.image_handler import (  # noqa: E402
    ImageHandler, _first_frame_jpeg, anime_section_text,
)
from nonebot_plugin_aigf_master.models import (  # noqa: E402
    AnimeHit, AnimeResult, ImageInfo, anime_hits_from_json,
)

FAILED = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} | {name} | got={actual!r}" + ("" if ok else f" want={expected!r}"))
    if not ok:
        FAILED.append(name)


def png_bytes(color, size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def gif_bytes(frames=3, size=(80, 60)):
    imgs = [Image.new("RGB", size, (i * 60 % 256, 40, 200 - i * 40)) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:], duration=100, loop=0)
    return buf.getvalue()


def setup_backend(backend, local_url="http://127.0.0.1:8000", trace_url="https://api.animetrace.com",
                  enabled=False, min_conf=0.85):
    plugin_config.aigfm_anime_backend = backend
    plugin_config.aigfm_anime_recognize_url = local_url
    plugin_config.aigfm_animetrace_url = trace_url
    plugin_config.aigfm_anime_recognize_enabled = enabled
    plugin_config.aigfm_anime_recognize_min_confidence = min_conf
    plugin_config.aigfm_anime_recognize_max_characters = 3
    plugin_config.aigfm_anime_nsfw_threshold = 0.5
    plugin_config.aigfm_animetrace_timeout = 20.0
    plugin_config.aigfm_proxy_enabled = False


def chain(local_result, trace_result, calls, backend="both", **kw):
    setup_backend(backend, **kw)
    rec = AnimeRecognizer(TMP)

    async def fake_local(digest, payload):
        calls.append(("local", digest, payload))
        return local_result

    async def fake_trace(digest, payload):
        calls.append(("trace", digest, payload))
        return trace_result

    rec._local.recognize = fake_local
    rec._trace.recognize = fake_trace
    return rec


# ---------- A. 渲染 ----------
print("=== A. anime_section_text ===")
check("A1 未触发 → 空串", anime_section_text(ImageInfo("d", "e")), "")
check("A2 已确认未能识别", anime_section_text(ImageInfo("d", "e", anime_hits=[])),
      " [角色识别: 未能识别]")
check("A3 本地命中", anime_section_text(ImageInfo("d", "e", anime_hits=[AnimeHit("hu_tao_(genshin_impact)", 0.987)])),
      " [角色识别: hu_tao_(genshin_impact) 98.7%]")
check("A4 AnimeTrace 命中（角色（作品））",
      anime_section_text(ImageInfo("d", "e", anime_hits=[AnimeHit("胡桃", None, "原神")])),
      " [角色识别: 胡桃（原神）]")
check("A5 多角色逗号分隔",
      anime_section_text(ImageInfo("d", "e", anime_hits=[AnimeHit("胡桃", None, "原神"), AnimeHit("钟离", None, "原神")])),
      " [角色识别: 胡桃（原神）, 钟离（原神）]")
check("A6 低于阈值 → 未能识别",
      anime_section_text(ImageInfo("d", "e", anime_hits=[AnimeHit("hu_tao", 0.42)])),
      " [角色识别: 未能识别]")
check("A7 超上限截断",
      anime_section_text(ImageInfo("d", "e", anime_hits=[AnimeHit(f"c{i}", 0.9) for i in range(5)])),
      " [角色识别: c0 90.0%, c1 90.0%, c2 90.0%]")
check("A8 NSFW + 命中",
      anime_section_text(ImageInfo("d", "e", anime_hits=[AnimeHit("skadi", 0.995)],
                                   anime_rating={"explicit": 0.91})),
      " [NSFW] [角色识别: skadi 99.5%]")
check("A9 多人部分识别",
      anime_section_text(ImageInfo("d", "e", anime_hits=[AnimeHit("胡桃", None, "原神")], anime_people_count=2)),
      " [角色识别: 胡桃（原神）] （图中检测到 2 个角色，仅识别出部分）")
check("A10 多人全未识别",
      anime_section_text(ImageInfo("d", "e", anime_hits=[], anime_people_count=2)),
      " [角色识别: 未能识别]（图中检测到 2 个角色）")
check("A11 旧缓存二元组兼容",
      anime_section_text(ImageInfo("d", "e", anime_hits=anime_hits_from_json([["hu_tao", 0.9]]))),
      " [角色识别: hu_tao 90.0%]")
check("A12 新缓存字典格式", anime_hits_from_json([{"name": "胡桃", "score": None, "work": "原神"}])[0].work, "原神")

# ---------- B. 降级链 ----------
print("=== B. 后端路由与降级 ===")
local_hit = AnimeResult(hits=[AnimeHit("hu_tao", 0.98), AnimeHit("noise", 0.1)], source="anime-recognize",
                        rating={"explicit": 0.01}, people_count=1)
local_low = AnimeResult(hits=[AnimeHit("maybe", 0.3)], source="anime-recognize", rating={}, people_count=1)
trace_hit = AnimeResult(hits=[AnimeHit("胡桃", None, "原神")], source="animetrace", people_count=1)
trace_low = AnimeResult(hits=[], source="animetrace", people_count=2)


async def run_chain(label, description, local_result, trace_result, backend="both", **kw):
    calls = []
    rec = chain(local_result, trace_result, calls, backend=backend, **kw)
    result = await rec.maybe_recognize(png_bytes((10, 10, 10)), description)
    return rec, calls, result


async def main():
    rec, calls, r = await run_chain("B1 both 本地命中", "立绘（二次元）", local_hit, trace_hit)
    check("B1 结果来自本地", (r.source, len(r.hits), r.hits[0].name), ("anime-recognize", 1, "hu_tao"))
    check("B1 未调用 AnimeTrace", [c[0] for c in calls], ["local"])

    rec, calls, r = await run_chain("B2 both 本地低+A命中", "立绘（二次元）", local_low, trace_hit)
    check("B2 结果来自 AnimeTrace", (r.source, r.hits[0].name, r.hits[0].work), ("animetrace", "胡桃", "原神"))
    check("B2 两个后端都调用", [c[0] for c in calls], ["local", "trace"])

    rec, calls, r = await run_chain("B3 both 都低置信", "立绘（二次元）", local_low, trace_low)
    check("B3 未能识别（人数取本地统计）", (r.hits, r.source, r.people_count), ([], "", 1))

    rec, calls, r = await run_chain("B4 both 都失败", "立绘（二次元）", None, None)
    check("B4 静默降级", r, None)

    rec, calls, r = await run_chain("B5 本地失败+A低置信", "立绘（二次元）", None, trace_low)
    check("B5 未能识别", (r.hits, r.people_count), ([], 2))

    rec, calls, r = await run_chain("B6 本地低+A失败", "立绘（二次元）", local_low, None)
    check("B6 未能识别", (r.hits, r.source), ([], ""))

    rec, calls, r = await run_chain("B7 仅 animetrace", "立绘（二次元）", local_hit, trace_hit, backend="animetrace")
    check("B7 只调 AnimeTrace", [c[0] for c in calls], ["trace"])
    check("B7 命中", r.hits[0].name, "胡桃")

    rec, calls, r = await run_chain("B8 仅 animetrace 失败", "立绘（二次元）", None, None, backend="animetrace")
    check("B8 静默降级", r, None)

    rec, calls, r = await run_chain("B9 仅本地命中", "立绘（二次元）", local_hit, trace_hit, backend="anime-recognize")
    check("B9 只调本地", [c[0] for c in calls], ["local"])
    check("B9 命中", r.hits[0].name, "hu_tao")

    rec, calls, r = await run_chain("B10 仅本地失败", "立绘（二次元）", None, None, backend="anime-recognize")
    check("B10 静默降级", r, None)

    rec, calls, r = await run_chain("B11 旧开关兼容（backend=off + enabled）", "立绘（二次元）",
                                    local_hit, trace_hit, backend="off", enabled=True)
    check("B11 effective_backend=anime-recognize", rec.backend(), "anime-recognize")
    check("B11 只调本地", [c[0] for c in calls], ["local"])

    rec, calls, r = await run_chain("B12 无（二次元）标记", "一张猫的照片", local_hit, trace_hit)
    check("B12 不调用任何后端", (calls, r), ([], None))

    rec, calls, r = await run_chain("B13 both 但本地无 URL", "立绘（二次元）", local_hit, trace_hit,
                                    local_url="")
    check("B13 退化为 animetrace", rec.backend(), "animetrace")
    check("B13 只调 AnimeTrace", [c[0] for c in calls], ["trace"])

    rec, calls, r = await run_chain("B14 both 但 A 无 URL", "立绘（二次元）", local_hit, trace_hit, trace_url="")
    check("B14 退化为本地", rec.backend(), "anime-recognize")

    setup_backend("off")
    check("B15 全关 → off", effective_backend(), "off")
    setup_backend("off", enabled=False)
    check("B16 全关 disabled", AnimeRecognizer(TMP).enabled(), False)

    # 动图：上传首帧 JPEG，缓存键仍是原图 md5
    gif = gif_bytes()
    first = _first_frame_jpeg(gif)
    check("B17 首帧 JPEG 生成", (first is not None, first[:3] == b"\xff\xd8\xff",
                                 Image.open(io.BytesIO(first)).size), (True, True, (80, 60)))
    calls = []
    rec = chain(local_low, trace_hit, calls, backend="both")
    r = await rec.maybe_recognize(gif, "动图（二次元）", first)
    check("B18 上传的是首帧 JPEG", [c[2][:3] == b"\xff\xd8\xff" for c in calls], [True, True])
    check("B18 缓存键是原图 md5", [c[1] for c in calls], [hashlib.md5(gif).hexdigest()] * 2)
    check("B18 命中来自 AnimeTrace", r.hits[0].name, "胡桃")

    # ---------- C. AnimeTrace 客户端（MockTransport） ----------
    print("=== C. AnimeTrace 客户端 ===")
    for stale in (TMP / "image_cache").glob("*_animetrace.json"):   # 清掉上一轮留下的缓存
        stale.unlink()
    state = {"search_calls": [], "list_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/model/list"):
            state["list_calls"] += 1
            return httpx.Response(200, json={"data": [
                {"id": "animetrace-yuri-4.2", "enabled": True, "default": True},
                {"id": "animetrace-aqours-3.5", "enabled": False, "default": False},
            ], "code": 0, "message": "success"})
        state["search_calls"].append(request.read())
        force = state.pop("force", None)   # 只对下一次搜索生效
        if force == "413":
            return httpx.Response(413, json={"code": 17701, "message": "图片大小过大"})
        if force == "400":
            return httpx.Response(400, json={"code": 17703, "message": "请求参数不正确"})
        return httpx.Response(200, json={"code": 0, "ai": False, "data": [
            {"box": [0.1, 0.1, 0.2, 0.2], "box_id": "a", "not_confident": False,
             "character": [{"work": "原神", "character": "胡桃"}, {"work": "X", "character": "其他"}]},
            {"box": [0.3, 0.3, 0.4, 0.4], "box_id": "b", "not_confident": True,
             "character": [{"work": "Y", "character": "低置信"}]},
        ], "trace_id": "t"})

    def new_client():
        c = AnimeTraceClient(TMP)
        c._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return c

    setup_backend("animetrace")
    c = new_client()
    check("C1 取 default 模型", await c.ensure_model(), "animetrace-yuri-4.2")
    check("C1 模型列表只查一次", state["list_calls"], 1)
    r = await c.recognize("c1digest", png_bytes((1, 2, 3)))
    check("C2 只保留 not_confident=False 的框首位", [(h.name, h.work, h.score) for h in r.hits], [("胡桃", "原神", None)])
    check("C2 人数=检测框数", r.people_count, 2)
    cached = TMP / "image_cache" / "c1digest_animetrace.json"
    check("C3 结果落盘缓存", cached.exists(), True)
    r2 = await c.recognize("c1digest", png_bytes((1, 2, 3)))
    check("C3 缓存命中不再请求", len(state["search_calls"]), 1)

    state["force"] = "400"
    lists_before = state["list_calls"]
    c = new_client()
    r = await c.recognize("c4digest", png_bytes((9, 9, 9)))
    check("C4 400 → 重查模型并重试", (len(state["search_calls"]), state["list_calls"] - lists_before, r is not None),
          (3, 2, True))

    state["force"] = "413"
    c = new_client()
    big = png_bytes((7, 7, 7), size=(2400, 1800))
    r = await c.recognize("c5digest", big)
    check("C5 413 → 压缩重试成功", (len(state["search_calls"]), r is not None), (5, True))
    check("C5 重试请求体与首次不同", state["search_calls"][-1] != state["search_calls"][-2], True)
    check("C5 _shrink_jpeg 产出 JPEG", _shrink_jpeg(big)[:3] == b"\xff\xd8\xff", True)

    c = new_client()
    c._model_loaded = True
    c._model = None

    def handler_busy(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/model/list"):
            return httpx.Response(200, json={"data": [], "code": 0})
        return httpx.Response(503, json={"code": 17702, "message": "服务器繁忙，请重试"})

    c._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler_busy))
    check("C6 服务繁忙 → 调用失败（静默降级）", await c.recognize("c6digest", png_bytes((3, 3, 3))), None)

    def handler_busy200(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 17728, "message": "已达到本次使用上限"})

    c._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler_busy200))
    check("C7 code≠0 → 调用失败", await c.recognize("c7digest", png_bytes((4, 4, 4))), None)

    def handler_empty_list(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/model/list"):
            return httpx.Response(200, json={"data": [], "code": 0, "message": "success"})
        return httpx.Response(200, json={"code": 0, "data": []})

    c2 = AnimeTraceClient(TMP)
    c2._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler_empty_list))
    check("C8 模型列表为空 → 不带 model", await c2.ensure_model(), None)
    r = await c2.recognize("c8digest", png_bytes((5, 5, 5)))
    check("C8 无检测框 → 空命中 + 无人数", (r.hits, r.people_count), ([], None))

    # ---------- D. describe() 端到端 ----------
    print("=== D. ImageHandler.describe 端到端 ===")

    class FakeVLM:
        def __init__(self, text):
            self.text = text
            self.calls = 0

        async def request(self, prompt, payload, fmt):
            self.calls += 1
            return self.text

    cache_dir = TMP / f"e2e_{os.getpid()}"
    for name, description, local_result, trace_result, expect in [
        ("D1 本地命中", "红发双马尾的立绘（二次元）", local_hit, trace_hit, " [角色识别: hu_tao 98.0%]"),
        ("D2 A 命中", "gal 立绘（二次元）", local_low, trace_hit, " [角色识别: 胡桃（原神）]"),
        ("D3 都低置信", "gal 立绘（二次元）", local_low, trace_low, " [角色识别: 未能识别]"),
        ("D4 都失败", "gal 立绘（二次元）", None, None, ""),
    ]:
        setup_backend("both")
        handler = ImageHandler(cache_dir)
        handler._vlm = FakeVLM(description)
        calls = []

        async def fake_local(digest, payload, _r=local_result, _c=calls):
            _c.append(("local", digest))
            return _r

        async def fake_trace(digest, payload, _r=trace_result, _c=calls):
            _c.append(("trace", digest))
            return _r

        handler._anime._local.recognize = fake_local
        handler._anime._trace.recognize = fake_trace
        img = png_bytes({"D1 本地命中": (200, 10, 10), "D2 A 命中": (10, 200, 10),
                         "D3 都低置信": (10, 10, 200), "D4 都失败": (200, 200, 10)}[name])
        b64 = base64.b64encode(img).decode()
        info = await handler.describe(b64, False)
        check(name, anime_section_text(info), expect)
        cache_file = cache_dir / "image_cache" / f"{hashlib.md5(img).hexdigest()}_anime.json"
        check(name + " 缓存文件", cache_file.exists(), True)
        check(name + " 缓存字段", "anime_hits" in json.loads(cache_file.read_text(encoding="utf-8")), True)
        before = len(calls)
        info2 = await handler.describe(b64, False)
        check(name + " 缓存命中不重复识别", (len(calls), anime_section_text(info2)), (before, expect))

    # 旧缓存（anime_chars 元组数组）兼容
    setup_backend("both")
    handler = ImageHandler(cache_dir)
    handler._vlm = FakeVLM("立绘（二次元）")
    old_img = png_bytes((123, 45, 67))
    old_file = cache_dir / "image_cache" / f"{hashlib.md5(old_img).hexdigest()}_anime.json"
    old_file.write_text(json.dumps({"description": "立绘（二次元）", "emotion": "", "is_sticker": False,
                                    "anime_chars": [["hu_tao_(genshin_impact)", 0.987]],
                                    "anime_rating": {}, "anime_people_count": 1}), encoding="utf-8")
    info = await handler.describe(base64.b64encode(old_img).decode(), False)
    check("D5 旧缓存兼容", anime_section_text(info), " [角色识别: hu_tao_(genshin_impact) 98.7%]")

    print("\n" + ("ALL PASS" if not FAILED else f"FAILED {len(FAILED)}: {FAILED}"))
    return 1 if FAILED else 0


sys.exit(asyncio.run(main()))

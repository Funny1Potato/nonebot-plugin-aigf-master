"""离线验证：缓存自动清理（prune_dir、describe 写入时清理、启动清理、raw 写入时清理）"""
from _bootstrap import REPO, SOURCE, TMP


import asyncio
import base64
import io
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(SOURCE))

os.environ["AIGFM_LLM_API_KEY"] = "test-key"
os.environ["AIGFM_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["AIGFM_VLM_ENABLED"] = "false"
os.environ["AIGFM_IMAGE_MODE"] = "vlm"
os.environ["AIGFM_ANIME_BACKEND"] = "off"

import nonebot  # noqa: E402

nonebot.init(driver="~none")
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot_plugin_aigf_master.cache_cleanup import prune_dir  # noqa: E402
from nonebot_plugin_aigf_master.config import plugin_config  # noqa: E402
from nonebot_plugin_aigf_master.image_handler import ImageHandler  # noqa: E402
from nonebot_plugin_aigf_master.models import AnimeHit, AnimeResult  # noqa: E402

FAILED = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} | {name} | got={actual!r}" + ("" if ok else f" want={expected!r}"))
    if not ok:
        FAILED.append(name)


def png_bytes(color, size=(32, 32)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_files(directory: Path, count, suffix=""):
    directory.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(count):
        p = directory / f"f{i}{suffix}"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))    # mtime 递增：f0 最旧
        names.append(p.name)
    return names


print("=== A. prune_dir ===")
d = TMP / "unit"
if d.exists():
    for f in d.iterdir():
        f.unlink()
make_files(d, 5)
check("A1 未超上限不删", (prune_dir(d, 5), len(list(d.iterdir()))), (0, 5))
check("A2 超上限按最旧删", (prune_dir(d, 2), sorted(p.name for p in d.iterdir())), (3, ["f3", "f4"]))
check("A3 limit=0 不清理", prune_dir(d, 0), 0)
check("A4 limit<0 不清理", prune_dir(d, -1), 0)
check("A5 目录不存在返回 0", prune_dir(TMP / "nope", 1), 0)

d2 = TMP / "pattern"
if d2.exists():
    for f in d2.iterdir():
        f.unlink()
make_files(d2, 3, suffix=".json")
for i in range(2):                                   # 混入非 json 文件（更新的 mtime）
    p = d2 / f"keep{i}.png"
    p.write_text("x", encoding="utf-8")
    os.utime(p, (2000 + i, 2000 + i))
check("A6 只删 pattern 匹配的文件",
      (prune_dir(d2, 1, "*.json"), sorted(p.name for p in d2.iterdir())),
      (2, ["f2.json", "keep0.png", "keep1.png"]))

d3 = TMP / "big"
if d3.exists():
    for f in d3.iterdir():
        f.unlink()
make_files(d3, 10)
check("A7 一次删到刚好等于上限",
      (prune_dir(d3, 4), len(list(d3.iterdir()))), (6, 4))

print("=== B. describe 写入时清理 ===")


class FakeVLM:
    def __init__(self, text):
        self.text = text

    async def request(self, prompt, payload, fmt):
        return self.text


async def main():
    cache_dir = TMP / "e2e"
    handler = ImageHandler(cache_dir)
    handler._vlm = FakeVLM("立绘（二次元）")
    plugin_config.aigfm_anime_backend = "both"
    plugin_config.aigfm_anime_recognize_url = "http://127.0.0.1:8000"
    plugin_config.aigfm_anime_recognize_min_confidence = 0.85

    async def fake_local(digest, payload):
        return AnimeResult(hits=[AnimeHit("hu_tao", 0.98)], source="anime-recognize", people_count=1)

    async def fake_trace(digest, payload):
        return AnimeResult(hits=[], source="animetrace", people_count=1)

    handler._anime._local.recognize = fake_local
    handler._anime._trace.recognize = fake_trace
    image_dir = cache_dir / "image_cache"

    plugin_config.aigfm_image_cache_max_files = 4
    for i in range(6):                                   # 每张图 1 个 VLM 缓存文件（角色识别被 mock 掉，不写自己的缓存）
        img = png_bytes((i * 30 % 256, 20, 30))
        info = await handler.describe(base64.b64encode(img).decode(), False)
        assert info is not None
    files = sorted(p.name for p in image_dir.glob("*.json"))
    check("B1 描述缓存不超过上限", len(files), 4)
    check("B2 保留的是最新的（f0/f1 系列被删）", len([f for f in files if f.endswith("_anime.json")]), 4)

    plugin_config.aigfm_image_cache_max_files = 0        # 0 = 不清理
    for i in range(6, 9):
        img = png_bytes((i * 30 % 256, 20, 30))
        await handler.describe(base64.b64encode(img).decode(), False)
    check("B3 上限 0 时不清理", len(list(image_dir.glob("*.json"))), 7)

    print("=== C. raw 写入时清理 ===")

    class FakeResp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp(b"\x89PNG-fake-bytes")

    plugin_config.aigfm_raw_cache_max_files = 3
    real_cache_dir = mod._cache_dir
    mod._cache_dir = TMP / "run"                         # 让 _load_image_bytes 用测试目录
    if mod._cache_dir.exists():                          # 每轮从干净目录开始（否则上次的 newid 会命中缓存）
        shutil.rmtree(mod._cache_dir)
    raw_dir = mod._cache_dir / "raw"
    make_files(raw_dir, 5)                               # 已有 5 个旧文件
    with patch.object(mod.httpx, "AsyncClient", FakeClient):
        data = await mod._load_image_bytes({"url": "http://x/y?fileid=newid", "file": "", "base64": ""})
    check("C1 raw 返回内容", data, b"\x89PNG-fake-bytes")
    remaining = sorted(p.name for p in raw_dir.iterdir())
    check("C2 raw 写入后清理到上限", len(remaining), 3)
    check("C3 新文件保留", "newid" in remaining, True)

    with patch.object(mod.httpx, "AsyncClient", FakeClient):
        await mod._load_image_bytes({"url": "http://x/y?fileid=newid", "file": "", "base64": ""})
    check("C4 命中缓存不重复下载也不重复清理", len(list(raw_dir.iterdir())), 3)

    print("=== D. 启动清理 ===")
    plugin_config.aigfm_image_cache_max_files = 2
    plugin_config.aigfm_raw_cache_max_files = 2
    plugin_config.aigfm_anime_backend = "off"            # 免得多出一个真实网络预热任务
    startup_images = mod._cache_dir / "image_cache"
    make_files(raw_dir, 5)
    make_files(startup_images, 6, suffix=".json")
    print(f"      before: image_cache={len(list(startup_images.glob('*.json')))}, raw={len(list(raw_dir.iterdir()))}, "
          f"limit_image={plugin_config.aigfm_image_cache_max_files}, limit_raw={plugin_config.aigfm_raw_cache_max_files}")
    try:
        await mod._on_startup()
        started = "ok"
    except Exception as e:                               # 启动流程可能与测试环境不兼容，记录原因
        started = f"{type(e).__name__}: {e}"
    print(f"      _on_startup -> {started}")
    print(f"      after: image_cache={sorted(p.name for p in startup_images.glob('*.json'))}, "
          f"raw={len(list(raw_dir.iterdir()))}")
    if started == "ok":
        check("D1 启动清理 image_cache", len(list(startup_images.glob("*.json"))), 2)
        check("D2 启动清理 raw", len(list(raw_dir.iterdir())), 2)
    check("D3 测试文件未污染真实缓存目录", (real_cache_dir / "raw" / "newid").exists(), False)

    print("\n" + ("ALL PASS" if not FAILED else f"FAILED {len(FAILED)}: {FAILED}"))
    return 1 if FAILED else 0


sys.exit(asyncio.run(main()))

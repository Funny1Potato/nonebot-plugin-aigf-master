"""离线验证：beta 回填 main 1.6.3/1.6.4（文本 @ 转换 / 多模态默认 / MIME / id 锚点 / 生效模式）"""
from _bootstrap import REPO, SOURCE, TMP, close_loop


import asyncio
import base64
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
    aigfm_enabled_groups=[617770183],
)
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot_plugin_aigf_master import session as S  # noqa: E402
from nonebot_plugin_aigf_master import unimsg as U  # noqa: E402
from nonebot_plugin_aigf_master.llm_client import (  # noqa: E402
    ImagePayload, _build_user_message, _has_image, _is_content_rejected, _sniff_image_mime,
)
from nonebot_plugin_aigf_master.config import plugin_config  # noqa: E402

PASSED = 0
FAILED = []


def check(name, actual, expected):
    global PASSED
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} | {name} | got={actual!r}" + ("" if ok else f" want={expected!r}"))
    if ok:
        PASSED += 1
    else:
        FAILED.append(name)


MEMBERS = [
    S.MemberName("张三", "1001"),
    S.MemberName("李四", "1002"),
    S.MemberName("Alice", "1003"),
    S.MemberName("王五", "1004"),
    S.MemberName("王五五", "1005"),
]

# ---------- A. split_text_mentions ----------
check("A1 无 @ 原样返回", S.split_text_mentions("你好呀", MEMBERS), [("text", "你好呀")])
check("A2 中文昵称", S.split_text_mentions("你好 @张三", MEMBERS),
      [("text", "你好 "), ("at", "1001")])
check("A3 全角＠", S.split_text_mentions("＠李四 在吗", MEMBERS),
      [("at", "1002"), ("text", " 在吗")])
check("A4 QQ 号", S.split_text_mentions("@1002 出来", MEMBERS),
      [("at", "1002"), ("text", " 出来")])
check("A5 匹配不到保留原文", S.split_text_mentions("@路人甲", MEMBERS), [("text", "@路人甲")])
check("A6 英文后接单词字符不拆", S.split_text_mentions("@Alice2", MEMBERS), [("text", "@Alice2")])
check("A7 英文后接空格可拆", S.split_text_mentions("@Alice hi", MEMBERS),
      [("at", "1003"), ("text", " hi")])
check("A8 长名字优先", S.split_text_mentions("@王五五 到", MEMBERS),
      [("at", "1005"), ("text", " 到")])
check("A9 多个 @", S.split_text_mentions("@张三 @李四", MEMBERS),
      [("at", "1001"), ("text", " "), ("at", "1002")])
check("A10 重名不转换",
      S.split_text_mentions("@同名", [S.MemberName("同名", "1"), S.MemberName("同名", "2")]),
      [("text", "@同名")])
check("A11 邮箱不误伤", S.split_text_mentions("a@张三.com", MEMBERS),
      [("text", "a"), ("at", "1001"), ("text", ".com")])
check("A12 空成员列表", S.split_text_mentions("@张三", []), [("text", "@张三")])

# ---------- B. compose_unimsg_list 文本 @ 转段 ----------
class _Bot:
    async def get_group_member_list(self, **_):
        return [{"user_id": 1001, "nickname": "张三", "card": ""},
                {"user_id": 1002, "nickname": "李四", "card": "李四同学"}]


info = S.SessionInfo(key="onebot11:617770183", slug="onebot11", scene_path=TMP / "memory",
                     native_chat_id="617770183", adapter_name="OneBot V11",
                     is_private=False, is_channel=False, scene_name="测试群",
                     scene_type="GROUP", member_scene_type="GROUP",
                     member_scene_id="617770183", user_id="1001",
                     user_name="张三", card="", target=None)

msgs, skipped = run(U.compose_unimsg_list(_Bot(), info, [
    {"type": "text", "content": "你好 @李四同学 在吗"},
]))
segs = msgs[0]
check("B1 文本 @ 转真实 at", [s.target for s in segs if isinstance(s, U.At)], ["1002"])
check("B2 其余文字仍保留", "在吗" in "".join(s.text for s in segs if isinstance(s, U.Text)), True)

msgs2, _ = run(U.compose_unimsg_list(_Bot(), info, [{"type": "text", "content": "@张三 你好"}]))
check("B3 昵称命中 at", [s.target for s in msgs2[0] if isinstance(s, U.At)], ["1001"])
check("B3b 文字跟在 at 后", "你好" in "".join(s.text for s in msgs2[0] if isinstance(s, U.Text)), True)

msgs3, skipped3 = run(U.compose_unimsg_list(_Bot(), info, [{"type": "at", "target": "李四"}]))
check("B4 at 段按群名片解析", skipped3, [])

msgs4, _ = run(U.compose_unimsg_list(None, None, [{"type": "text", "content": "@张三 你好"}]))
check("B5 无会话时保留文本 @", [type(s).__name__ for s in msgs4[0]], ["Text"])

# ---------- C. ImagePayload / MIME / 锚点 ----------
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()
GIF = base64.b64encode(b"GIF89a" + b"\x00" * 32).decode()
JPG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 32).decode()
WEBP = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16).decode()

check("C1 PNG MIME", _sniff_image_mime(PNG), "image/png")
check("C2 GIF MIME", _sniff_image_mime(GIF), "image/gif")
check("C3 JPEG MIME", _sniff_image_mime(JPG), "image/jpeg")
check("C4 WEBP MIME", _sniff_image_mime(WEBP), "image/webp")
check("C5 空数据回落 jpeg", _sniff_image_mime(""), "image/jpeg")

msg = _build_user_message("提示", [ImagePayload("abc123", PNG)])
check("C6 无图时是纯文本", _build_user_message("提示")["content"], "提示")
check("C7 有图时是多模态内容", isinstance(msg["content"], list), True)
check("C8 id 锚点在前", msg["content"][1], {"type": "text", "text": "[图片 id: abc123]"})
check("C9 真实 MIME", msg["content"][2]["image_url"]["url"].startswith("data:image/png;base64,"), True)
check("C10 _has_image 识别", _has_image([msg]), True)
check("C11 _has_image 纯文本", _has_image([{"role": "user", "content": "x"}]), False)


class _Err(Exception):
    def __init__(self, status):
        self.status_code = status


check("C12 400 视为内容被拒", _is_content_rejected(_Err(400)), True)
check("C13 415 视为内容被拒", _is_content_rejected(_Err(415)), True)
check("C14 429 不视为内容被拒", _is_content_rejected(_Err(429)), False)
check("C15 401 不视为内容被拒", _is_content_rejected(_Err(401)), False)
check("C16 无 status 不视为内容被拒", _is_content_rejected(Exception("x")), False)

# ---------- D. 多模态判定与生效模式 ----------
llm = mod._llm
check("D1 未判定时按支持处理", llm.multimodal, True)
llm.set_multimodal(False)
check("D2 判定不支持后 multimodal=False", llm.multimodal, False)
llm.set_multimodal(True)
check("D3 判定支持后 multimodal=True", llm.multimodal, True)

saved = plugin_config.aigfm_image_mode
plugin_config.aigfm_image_mode = "llm"
check("D4 llm + 支持 → 生效 llm", mod._effective_image_mode(), "llm")
llm.set_multimodal(False)
check("D5 llm + 不支持 → 生效 vlm", mod._effective_image_mode(), "vlm")
plugin_config.aigfm_image_mode = "vlm"
check("D6 显式 vlm 恒为 vlm", mod._effective_image_mode(), "vlm")
plugin_config.aigfm_image_mode = saved
llm.set_multimodal(True)

check("D7 config 默认图片模式为 llm", plugin_config.aigfm_image_mode, "llm")
check("D8 新增配置项存在", hasattr(plugin_config, "aigfm_llm_multimodal"), True)

proc = mod._get_processor("onebot11:617770183")
plugin_config.aigfm_image_mode = "llm"
llm.set_multimodal(False)
check("D9 processor 生效模式随主模型回落", proc._image_mode, "vlm")
llm.set_multimodal(True)
plugin_config.aigfm_image_mode = saved

print()
print(f"=== 结果: {PASSED} 通过 / {len(FAILED)} 失败 ===")
if FAILED:
    for name in FAILED:
        print("  -", name)
    sys.exit(1)

"""离线验证：跨适配器改造（会话键 / 通用消息渲染 / 捕获 / 调用 / 发送 / 迁移 / 第二适配器）"""
from _bootstrap import REPO, SOURCE, TMP, close_loop


import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

_LOOP = asyncio.new_event_loop()          # Python 3.14 起 get_event_loop() 不再自动建 loop
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
    aigfm_image_mode="vlm",   # B5-B7 校验 VLM 渲染路径（默认已改为 llm）
    aigfm_vlm_enabled=False,
    aigfm_anime_backend="off",
    aigfm_enabled_groups=[617770183, "onebot11:617770185"],
    aigfm_enabled_private=[20001],
    aigfm_batch_timeout=0.2,
    aigfm_incomplete_timeout=0.3,
    aigfm_batch_count=100,
)
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

# 启动钩子里才注册跨插件捕获钩子；测试里显式注册一次（与 _on_startup 相同的实参）
from nonebot_plugin_aigf_master.api_hooks import register_hooks  # noqa: E402
register_hooks(mod._bus, mod._image_handler, mod._add_external_message,
               mod._on_image_start, mod._on_image_done)

from nonebot import logger as nb_logger  # noqa: E402
from nonebot.adapters import Event  # noqa: E402
from nonebot.adapters.onebot.v11 import Bot as OB11Bot, Message as OB11Message, MessageSegment as OB11Segment, GroupMessageEvent, PrivateMessageEvent  # noqa: E402
from nonebot.internal.matcher import current_event, current_matcher  # noqa: E402
from nonebot.message import handle_event  # noqa: E402

from nonebot_plugin_aigf_master import session as S  # noqa: E402
from nonebot_plugin_aigf_master import unimsg as U  # noqa: E402
from nonebot_plugin_aigf_master.config import plugin_config  # noqa: E402
from nonebot_plugin_aigf_master.models import ImageInfo  # noqa: E402
from nonebot_plugin_aigf_master.plugin_invoker import PluginInvoker  # noqa: E402
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


# ========== 环境桩 ==========

class MockAdapter:
    def __init__(self, name="OneBot V11", message_class=OB11Message):
        self._name = name
        self._message_class = message_class
        self.config = nonebot.get_driver().config      # SUPERUSER 权限检查要读 superusers

    def get_name(self):
        return self._name

    def get_message_class(self):
        return self._message_class

    async def _call_api(self, bot, api, **data):
        return {}


MEMBERS = {
    20001: {"nickname": "张三", "card": "名片张", "role": "admin"},
    20002: {"nickname": "李四", "card": "", "role": "member"},
}


class MockBot(OB11Bot):
    """记录 API 调用并按 api 名返回桩数据"""

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
            return [
                {"user_id": u, "sex": "male", "join_time": 1600000000, **info}
                for u, info in MEMBERS.items()
            ]
        if api == "get_stranger_info":
            return {"user_id": uid, "nickname": f"用户{uid}", "sex": "male"}
        if api == "get_msg":
            return {"message": OB11Message("被回复的内容"),
                    "sender": {"user_id": 20002, "nickname": "李四"}}
        if api == "get_friend_list":
            return [{"user_id": 20001, "nickname": "张三", "remark": ""}]
        return {}


def make_bot(name="OneBot V11", message_class=OB11Message) -> MockBot:
    bot = MockBot(adapter=MockAdapter(name, message_class), self_id="10000")
    bot.calls = []
    return bot


_msg_seq = iter(range(1000, 100000))


def group_event(user_id=20001, group_id=617770183, text="hi", message=None, message_id=None,
                sender=None, extra_message=None):
    msg = message if message is not None else OB11Message(text)
    # 消息 id 必须唯一：alconna 按 message_id 缓存收到的消息，重复 id 会让响应器读到旧消息
    message_id = next(_msg_seq) if message_id is None else message_id
    return GroupMessageEvent(
        time=1700000000, self_id=10000, post_type="message", sub_type="normal",
        message_type="group", user_id=user_id, group_id=group_id, message_id=message_id,
        message=msg, raw_message=extra_message if extra_message is not None else msg.extract_plain_text(),
        font=0, sender=sender or {"user_id": user_id, "nickname": MEMBERS.get(user_id, {}).get("nickname", "张三"),
                                  "role": MEMBERS.get(user_id, {}).get("role", "member")},
    )


PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
           "q842iQAAAABJRU5ErkJggg==")
PNG_BYTES = base64.b64decode(PNG_B64)
PNG_ID = hashlib.md5(PNG_BYTES).hexdigest()[:12]


def session_for(event, bot=None):
    return run(resolve_session(bot or make_bot(), event))


# ========== A. 会话层 ==========

print("\n=== A. 会话层 ===")
check("A1 slug OneBot V11", S.adapter_slug("OneBot V11"), "onebot11")
check("A2 slug OneBot V12", S.adapter_slug("OneBot V12"), "onebot12")
check("A3 slug 未知适配器", S.adapter_slug("Some Adapter!"), "someadapter")
check("A4 会话键", S.session_key("onebot11", "617770183"), "onebot11:617770183")
check("A5 落盘键不含冒号", S.disk_key("onebot11:617770183"), "onebot11_617770183")
check("A6 裸 id 视为 onebot11", S.split_session(617770183), ("onebot11", "617770183"))
check("A7 带前缀拆分", S.split_session("telegram:123"), ("telegram", "123"))
check("A8 启用清单归一", sorted(S.normalize_enabled([617770183, "satori:abc"])),
      ["onebot11:617770183", "satori:abc"])
check("A9 空清单", S.normalize_enabled([]), set())

# 迁移
mem = mod._data_dir / "memory"
(mem / "617770183").mkdir(parents=True, exist_ok=True)
(mem / "617770184").mkdir(parents=True, exist_ok=True)
(mem / "onebot11_617770185").mkdir(parents=True, exist_ok=True)
(mem / "friends").mkdir(parents=True, exist_ok=True)
moved = S.migrate_legacy_dirs(mod._data_dir)
check("A10 迁移数量（已存在的不重复迁移）", moved, 2)
check("A11 迁移结果", sorted(p.name for p in mem.iterdir() if p.is_dir()),
      ["friends", "onebot11_617770183", "onebot11_617770184", "onebot11_617770185"])
check("A12 迁移幂等", S.migrate_legacy_dirs(mod._data_dir), 0)

# uninfo 解析（onebot 群消息）
ev = group_event()
bot = make_bot()
info = run(resolve_session(bot, ev))
check("A13 会话键", info.key, "onebot11:617770183")
check("A14 原生会话 id", info.native_chat_id, "617770183")
check("A15 数字 id", info.numeric_chat_id, 617770183)
check("A16 昵称", info.user_name, "张三")
check("A17 群名片", info.card, "名片张")
check("A18 群名", info.scene_name, "测试群")
check("A19 非私聊", info.is_private, False)
check_true("A20 有 Target", info.target is not None)
check("A21 启用判断", S.is_enabled(info, plugin_config), True)

# 私聊
priv = PrivateMessageEvent(
    time=1700000000, self_id=10000, post_type="message", sub_type="friend",
    message_type="private", user_id=20001, message_id=next(_msg_seq), message=OB11Message("你好"),
    raw_message="你好", font=0, sender={"user_id": 20001, "nickname": "张三", "role": "member"},
)
info_p = run(resolve_session(make_bot(), priv))
check("A22 私聊会话键", info_p.key, "onebot11:20001")
check("A23 私聊标记", info_p.is_private, True)
check("A24 私聊启用", S.is_enabled(info_p, plugin_config), True)

# 未启用会话
info_off = run(resolve_session(make_bot(), group_event(group_id=999)))
check("A25 未启用会话", S.is_enabled(info_off, plugin_config), False)

# ========== B. 消息渲染（onebot 全部段类型） ==========

print("\n=== B. 消息渲染 ===")


async def render(event, bot=None, desc=None):
    bot = bot or make_bot()
    if desc is not None:
        async def fake_describe(image_base64, is_sticker):
            return desc
        mod._image_handler.describe = fake_describe
    info = await resolve_session(bot, event)
    unimsg = await U.build_unimsg(bot, event)
    return await mod._parse_message(bot, info, unimsg, "小助手")


check("B1 纯文本", run(render(group_event(text="hello"))), ("hello", False))
check("B2 纯 @机器人（is_at_only）", run(render(group_event(message=OB11Message("[CQ:at,qq=10000]"))))[1], True)
check("B3 @机器人文案", run(render(group_event(message=OB11Message("[CQ:at,qq=10000]"))))[0], "@小助手")
check("B4 @群友文案", run(render(group_event(message=OB11Message("[CQ:at,qq=20002]"))))[0], "@李四(20002)")
check("B5 图片（VLM 失败）", run(render(group_event(message=OB11Message(f"[CQ:image,file=base64://{PNG_B64}]"))))[0],
      "[发送了一张图片]（识图失败）")
check("B6 图片（VLM 成功）",
      run(render(group_event(message=OB11Message(f"[CQ:image,file=base64://{PNG_B64}]")),
                 desc=ImageInfo(description="一只猫", emotion="开心")))[0],
      f"[发送了一张图片, id: {PNG_ID}] [内容:一只猫]")
check("B7 表情包图片",
      run(render(group_event(message=OB11Message(f"[CQ:image,file=base64://{PNG_B64},sub_type=1]")),
                 desc=ImageInfo(description="一只猫", emotion="开心", is_sticker=True)))[0],
      f"[发送了一张可能是表情包的图片, id: {PNG_ID}] [情感:开心] [内容:一只猫]")
check("B8 回复段",
      run(render(group_event(message=OB11Message("[CQ:reply,id=101] 看看"))))[0],
      '[回复 李四(20002) 的消息: "被回复的内容"]  看看')
check("B9 合并转发", run(render(group_event(message=OB11Message("[CQ:forward,id=f1]"))))[0], "[收到一条合并聊天记录]")
check("B10 xml", run(render(group_event(message=OB11Message("[CQ:xml,data=abc]"))))[0], "[收到一条XML消息]")
check("B11 json 小程序",
      run(render(group_event(message=OB11Message([OB11Segment.json('{"title":"标题","desc":"描述"}')]))))[0],
      "[小程序/卡片: 标题, 描述]")
check("B12 QQ表情带名字", run(render(group_event(message=OB11Message("[CQ:face,id=14,text=微笑]"))))[0], "[QQ表情 微笑]")
check("B13 QQ表情无名字", run(render(group_event(message=OB11Message("[CQ:face,id=14]"))))[0], "[QQ表情 14]")
check("B14 语音", run(render(group_event(message=OB11Message("[CQ:record,file=a.mp3]"))))[0], "[收到一条语音消息]")
check("B15 视频", run(render(group_event(message=OB11Message("[CQ:video,file=a.mp4]"))))[0], "[收到一条视频消息]")
check("B16 群文件带大小",
      run(render(group_event(message=OB11Message("[CQ:file,file_id=x,file_name=a.txt,file_size=2048]"))))[0],
      "[群文件: a.txt（2.0KB）]")
check("B17 分享链接",
      run(render(group_event(message=OB11Message("[CQ:share,title=标题,url=http://a.b,content=说明]"))))[0],
      "[分享链接: 标题 | http://a.b | 说明]")
check("B18 推荐群", run(render(group_event(message=OB11Message("[CQ:contact,type=group,id=123]"))))[0], "[推荐群: 123]")
check("B19 位置", run(render(group_event(message=OB11Message("[CQ:location,title=家,lat=1,lon=2]"))))[0], "[位置: 家 | 1,2]")
check("B20 音乐", run(render(group_event(message=OB11Message("[CQ:music,type=custom,title=歌名,url=http://x]"))))[0],
      "[音乐分享: 歌名]")
check("B21 骰子", run(render(group_event(message=OB11Message("[CQ:dice,result=3]"))))[0], "[骰子 3]")
check("B22 猜拳", run(render(group_event(message=OB11Message("[CQ:rps]"))))[0], "[猜拳]")
check("B23 窗口抖动", run(render(group_event(message=OB11Message("[CQ:shake]"))))[0], "[窗口抖动]")
check("B24 匿名消息", run(render(group_event(message=OB11Message("[CQ:anonymous,flag=a]"))))[0], "[匿名消息]")
check("B25 合并节点", run(render(group_event(message=OB11Message("[CQ:node,id=n1]"))))[0], "[合并聊天记录节点]")
check("B26 纯表情消息不丢弃",
      run(render(group_event(message=OB11Message("[CQ:face,id=1,text=微笑]"))))[1], False)
check("B27 组合消息（与 main 一致的双空格保留）", run(render(group_event(message=OB11Message("你好 [CQ:at,qq=20002] 在吗"))))[0],
      "你好  @李四(20002)  在吗")
check("B28 未知段兜底", run(render(group_event(message=OB11Message("[CQ:unknown_thing,a=1]"))))[0],
      "[其他消息段: unknown_thing]")

# ========== C. 管理命令（真实分发） ==========

print("\n=== C. 管理命令 ===")

SUPER = 945126014


def dispatch(text, user_id=SUPER, group_id=617770183, event=None):
    """真实分发一条消息，返回机器人发出的文本列表"""
    bot = make_bot()
    ev = event if event is not None else group_event(user_id=user_id, group_id=group_id,
                                                     message=OB11Message(text))
    run(handle_event(bot, ev))
    out = []
    for api, data in bot.calls:
        if api == "send_msg":
            out.append(str(data.get("message")))
    return out


def spy_alconna():
    """打印 alconna 规则的解析结果（排查匹配问题时手工调用）"""
    from nonebot_plugin_alconna.rule import AlconnaRule
    orig_handle = AlconnaRule.handle

    async def spy_handle(self, selected, cmd, bot, event, state, msg):
        res = await orig_handle(self, selected, cmd, bot, event, state, msg)
        if cmd.name in ("set_role", "status"):
            arp = res if res is False else (res.head_matched, res.matched, dict(res.all_matched_args))
            print(f"      parse[{cmd.name}] {msg!r} -> {arp}")
        return res

    AlconnaRule.handle = spy_handle


_ = spy_alconna          # 保留工具函数备用


def check_cmd(name, text, marker, expect=True, **kw):
    msgs = dispatch(text, **kw)
    hit = any(marker in m for m in msgs)
    if hit != expect:
        print("      msgs:", msgs)
    check(name, hit, expect)


check_cmd("C1 status 命中", "status", "社交能量")
check_cmd("C2 中文别名命中", "状态", "社交能量")
check_cmd("C3 句中提及不命中", "我的 status 是什么", "社交能量", expect=False)
check_cmd("C4 普通聊天不命中", "今天天气不错", "社交能量", expect=False)
check_cmd("C5 状态词不误触发", "现在什么状态", "社交能量", expect=False)
check_cmd("C6 reset 命中", "reset", "已重置会话")
check_cmd("C7 重置 别名命中", "重置", "已重置会话")
check_cmd("C8 set_role 命中", "set_role 小助手 一个 友好的 群聊助手", "角色已设为")
check_cmd("C9 set_preset 命中", "set_preset default", "不存在的预设")
check_cmd("C10 reload_meme 命中", "reload_meme", "已重载")
check_cmd("C11 presets 命中", "presets", "用法: set_preset")
check_cmd("C12 私聊也可执行管理命令", "status", "社交能量",
          event=PrivateMessageEvent(
              time=1700000000, self_id=10000, post_type="message", sub_type="friend",
              message_type="private", user_id=SUPER, message_id=next(_msg_seq),
              message=OB11Message("status"), raw_message="status", font=0,
              sender={"user_id": SUPER, "nickname": "土豆", "role": "member"}))
check_cmd("C13 未启用群也可执行管理命令", "status", "社交能量", group_id=999)
check_cmd("C14 非 superuser 不响应管理命令", "status", "社交能量", user_id=20001, expect=False)

# set_role 参数解析（用插件自己持有的命令对象，避免另造一个同路径 Alconna）
role_parse = mod.set_role_cmd._rule.command().parse("set_role 小助手 一个 友好的 群聊助手")
check("C15 set_role 匹配", role_parse.matched, True)
if role_parse.matched:
    rest = [str(x) for x in role_parse.all_matched_args.get("rest") or ()]
    check("C16 set_role 名字", rest[0] if rest else None, "小助手")
    check("C17 set_role 设定为剩余整体", " ".join(rest[1:]), "一个 友好的 群聊助手")
check_cmd("C17b 缺角色时提示用法", "set_role 小助手", "用法: set_role")
check_cmd("C17c 只有命令时提示用法", "set_role", "用法: set_role")

# AlconnaRule 只持弱引用：命令对象必须被强引用，否则 GC 后命令静默失效
import gc as _gc  # noqa: E402
_gc.collect()
check("C22 GC 后命令对象仍在", mod.set_role_cmd._rule.command() is not None, True)
check_cmd("C23 GC 后 status 仍可用", "status", "社交能量")
_gc.collect()
check_cmd("C24 GC 后 set_role 仍可用", "set_role 小助手 设定文本", "角色已设为")
_gc.collect()
check_cmd("C25 GC 后短形式仍提示用法", "set_role 小助手", "用法: set_role")

# auto_chat 的启用预筛
check("C18 启用群预筛", mod._maybe_enabled(make_bot(), group_event()), True)
check("C19 未启用群预筛", mod._maybe_enabled(make_bot(), group_event(group_id=999)), False)
check("C20 启用私聊预筛", mod._maybe_enabled(make_bot(), priv), True)
check("C21 未启用私聊预筛", mod._maybe_enabled(make_bot(), priv.model_copy(update={"user_id": 29999})), False)

# ========== D. 图片计数与图片缓存键 ==========

print("\n=== D. 图片计数 / 缓存键 ===")
mod._pending_images.clear()
run(render(group_event(message=OB11Message(f"[CQ:image,file=base64://{PNG_B64}]")),
           desc=ImageInfo(description="猫", emotion="平静")))
check("D1 计数已归零", mod._pending_images.get("onebot11:617770183", 0), 0)
cached = mod._memes.get_cached("onebot11:617770183")
check("D2 缓存索引按会话键", [c["id"] for c in cached], [PNG_ID])

# ========== E. 跨插件捕获 ==========

print("\n=== E. 跨插件捕获 ===")


class FakeMatcher:
    def __init__(self, name):
        self.plugin_name = name


async def fire_hooks(bot, api, data):
    from nonebot.adapters import Bot
    hooks = list(Bot._calling_api_hook)
    for hook in hooks:
        await hook(bot, api, data)


def capture(api="send_msg", data=None, source="other_plugin", event=None, bot=None):
    mod._group_chunks.clear()
    mod._bus._buffers.clear()
    bot = bot or make_bot()
    tok_event = current_event.set(event if event is not None else group_event())
    tok_matcher = current_matcher.set(FakeMatcher(source))
    try:
        run(fire_hooks(bot, api, data if data is not None else {
            "group_id": 617770183, "message": OB11Message("插件输出")}))
    finally:
        current_event.reset(tok_event)
        current_matcher.reset(tok_matcher)
    return bot


bot = capture()
check("E1 文本捕获入缓冲", [m.content for m in mod._group_chunks["onebot11:617770183"]], ["插件输出"])
check("E2 文本捕获入总线", [m.content for m in mod._bus.get_recent("onebot11:617770183")], ["插件输出"])
check("E3 来源插件名", mod._bus.get_recent("onebot11:617770183")[0].source_plugin, "other_plugin")

mod._group_chunks.clear()
bot = capture(api="send_group_msg", data={"group_id": 617770183, "message": OB11Message("群消息接口")})
check("E4 send_group_msg 兼容", [m.content for m in mod._group_chunks["onebot11:617770183"]], ["群消息接口"])

bot = capture(data={"group_id": 617770183, "user_id": 20002})
check("E5 非发消息调用不捕获", mod._group_chunks.get("onebot11:617770183", []), [])

bot = capture(data={"group_id": 999, "message": OB11Message("未启用群")})
check("E6 未启用会话不捕获", mod._group_chunks.get("onebot11:999", []), [])

bot = capture(source="nonebot_plugin_aigf_master", data={"group_id": 617770183, "message": OB11Message("自己")})
check("E7 跳过自身", mod._group_chunks.get("onebot11:617770183", []), [])

plugin_config.aigfm_capture_plugins = ["allowed_plugin"]
bot = capture(source="not_allowed", data={"group_id": 617770183, "message": OB11Message("不应捕获")})
check("E8 白名单外不捕获", mod._group_chunks.get("onebot11:617770183", []), [])
bot = capture(source="allowed_plugin", data={"group_id": 617770183, "message": OB11Message("白名单内")})
check("E9 白名单内捕获", [m.content for m in mod._group_chunks["onebot11:617770183"]], ["白名单内"])
plugin_config.aigfm_capture_plugins = []

# 图片捕获
async def fake_describe(image_base64, is_sticker):
    return ImageInfo(description="插件图片", emotion="平静")


mod._image_handler.describe = fake_describe
mod._group_chunks.clear()
bot = capture(data={"group_id": 617770183,
                    "message": OB11Message(f"[CQ:image,file=base64://{PNG_B64}]")})
check("E10 插件图片入缓冲", [m.content for m in mod._group_chunks["onebot11:617770183"]], ["[图片] 插件图片"])
check("E11 图片计数归零", mod._pending_images.get("onebot11:617770183", 0), 0)

# 无事件上下文（回落 API 参数拼会话）
bot = capture(data={"group_id": 617770183, "message": OB11Message("无事件")}, event=priv)
check("E12 事件上下文优先（私聊事件 + 群参数 → 私聊会话）",
      [m.content for m in mod._group_chunks.get("onebot11:20001", [])], ["无事件"])

# 多段混合（文本 + 其他段）
mod._group_chunks.clear()
bot = capture(data={"group_id": 617770183,
                    "message": OB11Message("看这个 [CQ:share,title=好文,url=http://x]")})
check("E13 混合段渲染", [m.content for m in mod._group_chunks["onebot11:617770183"]],
      ["看这个 [分享链接: 好文 | http://x]"])

# ========== F. 插件调用（复制真实事件） ==========

print("\n=== F. 插件调用 ===")

from nonebot import on_command  # noqa: E402

mod._group_chunks.clear()          # 清掉 E 段捕获留下的缓冲
seen = {}


@on_command("dummy", priority=50).handle()
async def _dummy(bot, event):
    seen.update({
        "message": str(event.get_message()),
        "raw": getattr(event, "raw_message", None),
        "user_id": event.get_user_id(),
        "nickname": getattr(getattr(event, "sender", None), "nickname", None),
        "card": getattr(getattr(event, "sender", None), "card", None),
        "role": getattr(getattr(event, "sender", None), "role", None),
        "message_id": getattr(event, "message_id", None),
        "reply": getattr(event, "reply", "MISSING"),
        "synthetic": mod._invoker.is_synthetic(event),
    })


inv_bot = make_bot()
inv_event = group_event(user_id=20001, message=OB11Message("原始消息"), message_id=555)
inv_session = run(resolve_session(inv_bot, inv_event))
seen.clear()
res = run(mod._invoker.invoke(inv_bot, inv_session, inv_event, "dummy", timeout=5,
                              user_id=20001, parts=[{"type": "at", "target": "20002"},
                                                    {"type": "text", "content": "10"}]))
check("F1 投递成功", res, "ok")
check("F2 命令文本（含 at 段）", seen.get("message"), "dummy [CQ:at,qq=20002] 10")
check("F3 raw_message", seen.get("raw"), "dummy  10")
check("F4 身份", seen.get("user_id"), "20001")
check("F5 复制真实昵称", seen.get("nickname"), "张三")
check("F6 真实群名片", seen.get("card"), "名片张")
check("F7 真实角色", seen.get("role"), "admin")
check("F8 保留原消息 id", seen.get("message_id"), 555)
check("F9 合成标记", seen.get("synthetic"), True)
check("F10 原事件未被改动", inv_event.get_message().extract_plain_text(), "原始消息")
check("F11 合成事件未进缓冲（auto_chat 已跳过）", mod._group_chunks.get("onebot11:617770183", []), [])

# 换身份调用：昵称/角色随之更新
seen.clear()
run(mod._invoker.invoke(inv_bot, inv_session, inv_event, "dummy", timeout=5, user_id=20002))
check("F12 换身份 user_id", seen.get("user_id"), "20002")
check("F13 换身份昵称", seen.get("nickname"), "李四")
check("F14 换身份角色", seen.get("role"), "member")

# 无模板事件
check("F15 无模板事件返回 error", run(mod._invoker.invoke(inv_bot, inv_session, None, "dummy", timeout=1)), "error")

# 模板消息带引用时，合成事件里的 reply 会被清空
reply_template = GroupMessageEvent(
    time=1700000000, self_id=10000, post_type="message", sub_type="normal",
    message_type="group", user_id=20001, group_id=617770183, message_id=next(_msg_seq),
    message=OB11Message("带引用的消息"), raw_message="带引用的消息", font=0,
    sender={"user_id": 20001, "nickname": "张三", "role": "member"},
    reply={"message_id": 42, "message": OB11Message("被引用的内容"), "time": 1700000000,
           "message_type": "group", "real_id": 42,
           "sender": {"user_id": 20002, "nickname": "李四", "role": "member"}},
)
run(handle_event(make_bot(), reply_template))
check("F20 模板确实带引用", reply_template.reply is not None, True)
seen.clear()
run(mod._invoker.invoke(inv_bot, inv_session, reply_template, "dummy", timeout=5))
check("F21 合成事件清空 reply", seen.get("reply"), None)
check("F22 合成事件不继承原消息体", seen.get("message"), "dummy")

# alconna 写的目标插件：验证合成事件的 message_id 缓存被刷新（否则响应器会读到原用户消息）
from nonebot_plugin_alconna import Alconna, Args, CommandResult, MultiVar, on_alconna  # noqa: E402
alc_hits = []
alc_target = on_alconna(Alconna("alcdummy", Args["rest", MultiVar(str, "*")]), priority=50, block=True)


@alc_target.handle()
async def _(event: Event, result: CommandResult):
    alc_hits.append((str(event.get_message()), dict(result.result.all_matched_args)))


alc_bot = make_bot()
alc_event = group_event(text="这是原用户消息")
run(handle_event(alc_bot, alc_event))          # 让 alconna 缓存这条消息（管理命令的规则会走到）
alc_hits.clear()
res = run(mod._invoker.invoke(alc_bot, run(resolve_session(alc_bot, alc_event)), alc_event,
                              "alcdummy", timeout=5, parts=[{"type": "text", "content": "参数"}]))
check("F18 alconna 目标插件被调用", res, "ok")
check("F19 读到的是合成命令而非原消息", alc_hits[-1] if alc_hits else None,
      ("alcdummy 参数", {"rest": ("参数",)}))

# 命令前缀（COMMAND_START=["/"]）
nonebot.get_driver().config.command_start = {"/"}
check("F16 前缀补齐", __import__("nonebot_plugin_aigf_master.plugin_invoker", fromlist=["_apply_command_prefix"])
      ._apply_command_prefix("dummy"), "/dummy")
nonebot.get_driver().config.command_start = {""}
check("F17 空前缀不加前缀", __import__("nonebot_plugin_aigf_master.plugin_invoker", fromlist=["_apply_command_prefix"])
      ._apply_command_prefix("dummy"), "dummy")

# ========== G. 发送 / 段落拼装 ==========

print("\n=== G. 发送 ===")


async def compose(parts, ev=None, info=None):
    bot = make_bot()
    ev = ev or group_event()
    info = info or await resolve_session(bot, ev)
    msgs, skipped = await U.compose_unimsg_list(bot, info, parts)
    return bot, ev, info, msgs, skipped


bot, ev, info, msgs, skipped = run(compose([
    {"type": "text", "content": "决定"},
    {"type": "at", "target": "20002"},
    {"type": "text", "content": "  10  "},
]))
check("G1 单条消息", len(msgs), 1)
check("G2 段落内容（单空格 + strip）", str(msgs[0].export_sync(None, adapter="OneBot V11")),
      "决定 [CQ:at,qq=20002] 10")
check("G3 无跳过 at", skipped, [])

bot, ev, info, msgs, skipped = run(compose([{"type": "at", "target": "不存在的人"}, {"type": "text", "content": "x"}]))
check("G4 解析不出的 at 被跳过", skipped, ["不存在的人"])
check("G5 跳过 at 后仍发文本", str(msgs[0].export_sync(None, adapter="OneBot V11")), "x")

bot, ev, info, msgs, skipped = run(compose([
    {"type": "text", "content": "前缀"},
    {"type": "image", "path": str(TMP / "a.png")},
    {"type": "text", "content": "后缀"},
]))
check("G6 图片独立成条", len(msgs), 3)
check("G7 图片条", "[CQ:image" in str(msgs[1].export_sync(None, adapter="OneBot V11")), True)

# 真实发送（走 bot.send_msg → call_api）
send_bot = make_bot()
target = run(compose([{"type": "text", "content": "你好"}]))[2].target
run(U.send_unimsg(send_bot, target, U.UniMessage.text("你好")))
sent = [(api, data) for api, data in send_bot.calls if api in ("send_msg", "send_group_msg")]
check("G8 群发送 api", sent[0][0] if sent else None, "send_msg")
check("G9 群发送目标", sent[0][1].get("group_id") if sent else None, 617770183)
check("G10 群发送内容", str(sent[0][1].get("message")) if sent else None, "你好")

priv_send = make_bot()
pinfo = run(resolve_session(priv_send, priv))
run(U.send_unimsg(priv_send, pinfo.target, U.UniMessage.text("私聊")))
sent_p = [(api, data) for api, data in priv_send.calls if api == "send_msg"]
check("G11 私聊发送类型", sent_p[0][1].get("message_type") if sent_p else None, "private")
check("G12 私聊发送目标", sent_p[0][1].get("user_id") if sent_p else None, 20001)

# ========== H. 批处理端到端（mock LLM） ==========

print("\n=== H. 批处理端到端 ===")

import sys as _sys  # noqa: E402


def _hlog(message):
    text = message.record["message"]
    if any(k in text for k in ("[处理", "[LLM", "发送", "失败", "[调用", "[能量", "[命令")):
        _sys.stdout.write("      LOG " + text[:160] + "\n")
        _sys.stdout.flush()


nb_logger.add(_hlog, level="DEBUG", format="{message}")

REPLY = json.dumps({"reply": [{"type": "text", "content": "在的"}]}, ensure_ascii=False)


async def fake_chat_with_tools(*args, **kwargs):
    return REPLY, False


async def fake_chat(*args, **kwargs):
    return REPLY


mod._llm.chat_with_tools = fake_chat_with_tools
mod._llm.chat = fake_chat

mod._group_chunks.clear()
mod._group_bot.clear()
mod._group_event.clear()
mod._group_last_time.clear()
e2e_bot = make_bot()
e2e_event = group_event(text="在吗", message_id=777)


async def run_e2e():
    await handle_event(e2e_bot, e2e_event)
    await asyncio.sleep(0.1)
    check("H1 用户消息入缓冲（含昵称与 id）",
          [m.user_name for m in mod._group_chunks.get("onebot11:617770183", [])][:1], ["张三(20001)"])
    check("H2 首次触发（is_at_only 影响计时）", mod._group_chunks["onebot11:617770183"][0].content, "在吗")
    task = asyncio.create_task(mod._batch_processor("onebot11:617770183"))
    await asyncio.sleep(1.4)          # 1s 轮询 + 0.2s 超时
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    replies = [(api, data) for api, data in e2e_bot.calls if api == "send_msg"]
    check("H3 回复已发送", [(str(d.get("message"))) for _, d in replies], ["在的"])
    check("H4 缓冲已清空", mod._group_chunks.get("onebot11:617770183", []), [])
    check("H5 无图片残留计数", mod._pending_images.get("onebot11:617770183", 0), 0)


run(run_e2e())

# 未启用会话不进批处理
mod._group_chunks.clear()
off_event = group_event(text="不该被处理", group_id=999)
run(handle_event(make_bot(), off_event))
check("H6 未启用群不入缓冲", mod._group_chunks.get("onebot11:999", []), [])

# ========== I. 第二适配器（Console） ==========

print("\n=== I. 第二适配器 Console ===")
try:
    from nonebot.adapters.console import Message as ConsoleMessage, MessageEvent as ConsoleMessageEvent
    console_ok = True
except Exception as e:
    console_ok = False
    print("      console 适配器不可用:", e)

if console_ok:
    c_bot = make_bot(name="Console", message_class=ConsoleMessage)
    c_event = ConsoleMessageEvent(
        time=1700000000, self_id="console", type="message", detail_type="group",
        message_id="c1", message=ConsoleMessage("跨适配器你好"),
        user={"id": "u1", "nickname": "控制台用户"},
        channel={"id": "console-chat", "name": "控制台群"},
    )
    c_info = run(resolve_session(c_bot, c_event))
    check("I1 console 会话解析", c_info is not None, True)
    check("I2 console 会话键", c_info.key if c_info else None, "console:console-chat")
    check("I3 console 昵称", c_info.user_name if c_info else None, "控制台用户")
    c_unimsg = run(U.build_unimsg(c_bot, c_event))
    check("I4 console 通用化", c_unimsg is not None, True)
    c_text = run(U.render_incoming(c_bot, c_info, c_unimsg,
                                  U.RenderDeps(bot_name="小助手", render_image=None)))
    check("I5 console 渲染", c_text[0], "跨适配器你好")
    # 真实发送需要真正的 ConsoleBot（console exporter 里有 isinstance 断言），
    # 离线用 export 验证「回复能被正确导出成该适配器的消息」
    exported = run(U.UniMessage.text("console 回复测试").export(bot=c_bot))
    check("I6 console 导出回复", str(exported), "console 回复测试")

# ========== K. peer 推送的会话映射（0.4.0 新增 session 字段） ==========

print("\n=== K. peer 推送会话 ===")
mod._group_chunks.clear()
run(mod._handle_peer_capture(617770183, "botB", {"session": "onebot11:617770185", "text": "带 session 的推送"}))
check("K1 优先用 peer 给的 session 键",
      [m.content for m in mod._group_chunks.get("onebot11:617770185", [])], ["带 session 的推送"])
check("K2 不再落到 group_id 对应会话", mod._group_chunks.get("onebot11:617770183", []), [])
mod._group_chunks.clear()
run(mod._handle_peer_capture(617770183, "botB", {"text": "旧版只有 group_id"}))
check("K3 旧 peer（只有 group_id）按 onebot11 映射",
      [m.content for m in mod._group_chunks.get("onebot11:617770183", [])], ["旧版只有 group_id"])
mod._group_chunks.clear()
run(mod._handle_peer_capture(999, "botB", {"session": "onebot11:999", "text": "未启用会话"}))
check("K4 未启用会话不入缓冲", mod._group_chunks.get("onebot11:999", []), [])
mod._group_chunks.clear()
mod._peer_scanned_commands.clear()
run(mod._handle_peer_capture(617770183, "botB", {"session": "onebot11:617770183", "commands": [{"name": "x"}]}))
check("K5 命令清单仍被记录", mod._peer_scanned_commands.get("botB"), [{"name": "x"}])
mod._group_chunks.clear()

# ========== J. 启动流程 ==========

print("\n=== J. 启动流程 ===")
legacy = mod._data_dir / "memory" / "888888"
legacy.mkdir(parents=True, exist_ok=True)
try:
    run(mod._on_startup())
    check("J1 启动流程无异常", True, True)
except Exception as e:
    check("J1 启动流程无异常", f"{type(e).__name__}: {e}", True)
check("J2 启动了启用会话的批处理任务（群 2 + 私聊 1）", len(mod._tasks) >= 3, True)
check("J3 启动时迁移旧目录", (mod._data_dir / "memory" / "onebot11_888888").is_dir() and not legacy.exists(), True)
for task in list(mod._tasks):
    task.cancel()
mod._tasks.clear()
check("J4 任务已清理", len(mod._tasks), 0)

# ========== 结果 ==========

print("\n====================")
print(f"PASS {PASSED} | FAIL {len(FAILED)}")
if FAILED:
    print("失败项:")
    for name in FAILED:
        print("  -", name)
print("====================")
sys.exit(1 if FAILED else 0)

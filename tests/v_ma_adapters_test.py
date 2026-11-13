"""跨适配器实测 v2：修正导入路径 / mock bot 行为 / 私聊-only 适配器 / 往返测试方式"""
from _bootstrap import REPO, SOURCE, TMP, close_loop

import asyncio
import importlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_LOOP = asyncio.new_event_loop()          # Python 3.14 起 get_event_loop() 不再自动建 loop
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
    aigfm_enabled_groups=["onebot11:11001", "onebot12:11001", "console:chan-1", "satori:c-1",
                          "telegram:-100", "discord:10_2", "qq:go-1", "feishu:oc-1", "milky:11001",
                          "mirai:11001", "kaiheila:ch-1", "dodo:isl-1_ch-1", "kritor:11001",
                          "efchat:room-1", "yunhu:g-1", "bilibililive:12345"],
    aigfm_enabled_private=["onebot11:20001", "onebot12:20001", "console:user-1", "satori:u-1",
                           "telegram:20001", "discord:3", "qq:uo-1", "feishu:ou-1", "milky:20001",
                           "mirai:20001", "dodo:u-1", "kritor:20001", "mail:u1",
                           "minecraft:player-uuid", "wxmp:user001", "efchat:zhangsan"],
    aigfm_batch_timeout=0.2, aigfm_batch_count=100,
)
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot.adapters import Bot as NBBot, Event  # noqa: E402
from nonebot.internal.matcher import current_event, current_matcher  # noqa: E402
from nonebot.message import handle_event  # noqa: E402
from nonebot_plugin_alconna import At, Text, UniMessage, get_target  # noqa: E402

from nonebot_plugin_aigf_master import session as S  # noqa: E402
from nonebot_plugin_aigf_master import unimsg as U  # noqa: E402

# 只放行 Invoker/PeerAgent 的日志，便于看「构造调用事件失败」这类 ERROR
from nonebot import logger as _nb_logger  # noqa: E402


def _sink(message):
    text = message.record["message"]
    if text.startswith(("[Invoker]", "[PeerAgent]", "[会话]")):
        sys.stdout.write("      LOG " + text[:200] + "\n")


_nb_logger.add(_sink, level="DEBUG", format="{message}")

NOW = datetime.now()


def run(coro):
    return _LOOP.run_until_complete(coro)


class _Stub:
    def __getattr__(self, name):
        return self

    def __str__(self):
        return "stub"


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
    """真 Bot 类的子类，绕过 __init__；类上的所有公开方法都被实例级 recorder 覆盖

    实例属性优先于类方法，因此连适配器自己实现的 `send_*/get_*` 也会被拦下来记录，
    避免走到真实实现里（它们依赖未初始化的 bot 状态）。未特化的方法一律记录并返回 _Stub。
    """
    import inspect

    class RecorderBot(bot_cls):
        def __init__(self):
            pass

        def __getattr__(self, name):
            if name.startswith("__") or name in ("adapter", "self_id", "calls", "_api", "_fail"):
                raise AttributeError(name)

            async def _rec(*a, **kw):
                self.calls.append((name, a, kw))
                if name in self._fail:
                    raise _ActionFailed()
                if name in self._api:
                    return self._api[name]
                return _Stub()
            return _rec

        async def call_api(self, api, **data):
            if "call_api" in self._fail:
                raise _ActionFailed()
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
                raise _ActionFailed()
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


def _ActionFailed():
    try:
        from nonebot.exception import ActionFailed
        return ActionFailed(None)
    except Exception:
        return RuntimeError("ActionFailed")


def event_class(module_name, class_name):
    """事件类可能在包根或 .event 子模块"""
    module = importlib.import_module(module_name)
    if hasattr(module, class_name):
        return getattr(module, class_name)
    return getattr(importlib.import_module(module_name + ".event"), class_name)


# ========== 事件构造 ==========

def ob11_group(slug):
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
    return GroupMessageEvent(
        time=int(NOW.timestamp()), self_id=10000, post_type="message", sub_type="normal",
        message_type="group", user_id=20001, group_id=11001, message_id=101,
        message=Message("跨适配器你好"), raw_message="跨适配器你好", font=0,
        sender={"user_id": 20001, "nickname": "张三", "role": "member"})


def ob11_private(slug):
    from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
    return PrivateMessageEvent(
        time=int(NOW.timestamp()), self_id=10000, post_type="message", sub_type="friend",
        message_type="private", user_id=20001, message_id=102, message=Message("私聊你好"),
        raw_message="私聊你好", font=0,
        sender={"user_id": 20001, "nickname": "张三", "role": "member"})


def ob12_group(slug):
    from nonebot.adapters.onebot.v12 import BotSelf, GroupMessageEvent, Message
    return GroupMessageEvent(
        id="e1", time=NOW, type="message", detail_type="group", sub_type="normal",
        self=BotSelf(platform="qq", user_id="10000"), message_id="m1",
        message=Message("跨适配器你好"), alt_message="跨适配器你好", user_id="20001", group_id="11001")


def ob12_private(slug):
    from nonebot.adapters.onebot.v12 import BotSelf, Message, PrivateMessageEvent
    return PrivateMessageEvent(
        id="e2", time=NOW, type="message", detail_type="private", sub_type="normal",
        self=BotSelf(platform="qq", user_id="10000"), message_id="m2", message=Message("私聊你好"),
        alt_message="私聊你好", user_id="20001")


def console_group(slug):
    from nonebot.adapters.console import Message, MessageEvent
    from nonechat.model import Channel, User
    return MessageEvent(time=NOW, self_id="console", post_type="message",
                        user=User(id="user-1", nickname="张三"),
                        channel=Channel(id="chan-1", name="控制台群"),
                        message_id="m1", message=Message("跨适配器你好"))


def console_private(slug):
    from nonebot.adapters.console import Message, MessageEvent
    from nonechat.model import DIRECT, User
    return MessageEvent(time=NOW, self_id="console", post_type="message",
                        user=User(id="user-1", nickname="张三"), channel=DIRECT,
                        message_id="m2", message=Message("私聊你好"))


def satori_group(slug):
    cls = event_class("nonebot.adapters.satori", "MessageCreatedEvent")
    return cls.model_validate({
        "type": "message-created", "timestamp": int(NOW.timestamp() * 1000),
        "login": {"sn": 1, "status": 1, "adapter": "satori", "platform": "qq",
                  "user": {"id": "10000", "name": "bot"}},
        "channel": {"id": "c-1", "type": 0, "name": "频道"},
        "guild": {"id": "g-1", "name": "公会"},
        "user": {"id": "u-1", "name": "张三", "nick": "三儿"},
        "message": {"id": "m1", "content": "跨适配器你好"},
    })


def satori_private(slug):
    cls = event_class("nonebot.adapters.satori", "PrivateMessageCreatedEvent")
    return cls.model_validate({
        "type": "message-created", "timestamp": int(NOW.timestamp() * 1000),
        "login": {"sn": 1, "status": 1, "adapter": "satori", "platform": "qq",
                  "user": {"id": "10000", "name": "bot"}},
        "channel": {"id": "p-99", "type": 1},
        "user": {"id": "u-1", "name": "张三"},
        "message": {"id": "m2", "content": "私聊你好"},
    })


def telegram_group(slug):
    from nonebot.adapters.telegram import Message
    cls = event_class("nonebot.adapters.telegram", "GroupMessageEvent")
    return cls(message_id=1, date=int(NOW.timestamp()),
               chat={"id": -100, "type": "group", "title": "群"},
               **{"from": {"id": 20001, "is_bot": False, "first_name": "张", "last_name": "三",
                           "username": "zhangsan"}},
               message=Message("跨适配器你好"))


def telegram_private(slug):
    from nonebot.adapters.telegram import Message
    cls = event_class("nonebot.adapters.telegram", "PrivateMessageEvent")
    return cls(message_id=2, date=int(NOW.timestamp()),
               chat={"id": 20001, "type": "private"},
               **{"from": {"id": 20001, "is_bot": False, "first_name": "张"}},
               message=Message("私聊你好"))


def discord_group(slug):
    from nonebot.adapters.discord import GuildMessageCreateEvent
    return GuildMessageCreateEvent(
        id=1, channel_id=2, guild_id=10, content="跨适配器你好", timestamp=NOW,
        edited_timestamp=None, tts=False, mention_everyone=False, mentions=[], mention_roles=[],
        attachments=[], embeds=[], pinned=False, type=0,
        author={"id": 3, "username": "张三", "discriminator": "0", "avatar": None})


def discord_private(slug):
    from nonebot.adapters.discord import DirectMessageCreateEvent
    return DirectMessageCreateEvent(
        id=2, channel_id=3, content="私聊你好", timestamp=NOW, edited_timestamp=None, tts=False,
        mention_everyone=False, mentions=[], mention_roles=[], attachments=[], embeds=[],
        pinned=False, type=0,
        author={"id": 3, "username": "张三", "discriminator": "0", "avatar": None})


def qq_group(slug):
    cls = event_class("nonebot.adapters.qq", "GroupMessageCreateEvent")
    return cls(id="m1", content="跨适配器你好", timestamp="1", group_id="g-1", group_openid="go-1",
               author={"id": "a1", "bot": False, "member_openid": "o-1", "member_role": "member"})


def qq_private(slug):
    cls = event_class("nonebot.adapters.qq", "C2CMessageCreateEvent")
    return cls(id="m2", content="私聊你好", timestamp="1",
               author={"id": "a1", "user_openid": "uo-1", "username": "张三"})


def qq_guild(slug):
    cls = event_class("nonebot.adapters.qq", "MessageCreateEvent")
    return cls(id="m3", content="频道你好", timestamp="1", channel_id="c-1", guild_id="g-1",
               author={"id": "u-1", "username": "张三"})


def feishu_group(slug):
    cls = event_class("nonebot.adapters.feishu", "GroupMessageEvent")
    return cls.model_validate({
        "header": {"event_id": "h1", "event_type": "im.message.receive_v1", "create_time": "1",
                   "token": "t", "app_id": "a", "tenant_key": "tk"},
        "event": {"sender": {"sender_id": {"union_id": "un1", "user_id": "u1", "open_id": "ou-1"},
                             "sender_type": "user", "tenant_key": "tk"},
                  "message": {"message_id": "m1", "create_time": "1", "chat_id": "oc-1",
                              "chat_type": "group", "message_type": "text",
                              "content": '{"text": "跨适配器你好"}'}},
    })


def feishu_private(slug):
    cls = event_class("nonebot.adapters.feishu", "PrivateMessageEvent")
    return cls.model_validate({
        "header": {"event_id": "h2", "event_type": "im.message.receive_v1", "create_time": "1",
                   "token": "t", "app_id": "a", "tenant_key": "tk"},
        "event": {"sender": {"sender_id": {"union_id": "un1", "user_id": "u1", "open_id": "ou-1"},
                             "sender_type": "user", "tenant_key": "tk"},
                  "message": {"message_id": "m2", "create_time": "1", "chat_id": "oc-2",
                              "chat_type": "p2p", "message_type": "text",
                              "content": '{"text": "私聊你好"}'}},
    })


def milky_group(slug):
    cls = event_class("nonebot.adapters.milky", "GroupMessageEvent")
    return cls.model_validate({
        "time": 1, "self_id": "10000",
        "data": {"message_scene": "group", "peer_id": 11001, "message_seq": 9,
                 "sender_id": 20001, "time": 1,
                 "segments": [{"type": "text", "data": {"text": "跨适配器你好"}}],
                 "group": {"group_id": 11001, "group_name": "群", "member_count": 5,
                           "max_member_count": 200},
                 "group_member": {"user_id": 20001, "nickname": "张三", "sex": "male",
                                  "group_id": 11001, "card": "名片", "title": "", "level": 1,
                                  "role": "member", "join_time": 1, "last_sent_time": 1}},
    })


def milky_private(slug):
    cls = event_class("nonebot.adapters.milky", "FriendMessageEvent")
    return cls.model_validate({
        "time": 1, "self_id": "10000",
        "data": {"message_scene": "friend", "peer_id": 20001, "message_seq": 10, "sender_id": 20001,
                 "time": 1, "segments": [{"type": "text", "data": {"text": "私聊你好"}}],
                 "friend": {"user_id": 20001, "nickname": "张三", "sex": "male", "qid": "", "remark": "",
                            "category": {"category_id": 1, "category_name": "默认"}}},
    })


def mirai_group(slug):
    from nonebot.adapters.mirai import Message as MiraiMessage
    from nonebot.adapters.mirai.model.relationship import Group, Member, MemberPerm
    cls = event_class("nonebot.adapters.mirai", "GroupMessage")
    group = Group.model_construct(id=11001, name="群", account_perm=MemberPerm.Member)
    member = Member.model_construct(id=20001, name="张三", permission=MemberPerm.Member, group=group)
    ev = cls.model_construct(raw_message=[{"type": "Plain", "text": "跨适配器你好"}], sender=member,
                             group=group, message_id=1, time=NOW, reply=None, to_me=False,
                             message=None, original_message=None)
    ev.message = MiraiMessage("跨适配器你好")
    ev.original_message = MiraiMessage("跨适配器你好")
    return ev


def mirai_private(slug):
    from nonebot.adapters.mirai import Message as MiraiMessage
    from nonebot.adapters.mirai.model.relationship import Friend
    cls = event_class("nonebot.adapters.mirai", "FriendMessage")
    friend = Friend.model_construct(id=20001, nickname="张三", remark="")
    ev = cls.model_construct(raw_message=[{"type": "Plain", "text": "私聊你好"}], sender=friend,
                             message_id=2, time=NOW, reply=None, to_me=False, message=None,
                             original_message=None)
    ev.message = MiraiMessage("私聊你好")
    ev.original_message = MiraiMessage("私聊你好")
    return ev


def kaiheila_group(slug):
    from nonebot.adapters.kaiheila import Message
    cls = event_class("nonebot.adapters.kaiheila", "MessageEvent")
    return cls.model_validate({
        "post_type": "message", "message_type": "group", "channel_type": "GROUP", "type": 1,
        "sub_type": "group", "target_id": "ch-1", "author_id": "20001", "user_id": "20001",
        "open_id": "20001", "content": "跨适配器你好", "msg_id": "m1",
        "msg_timestamp": int(NOW.timestamp() * 1000), "nonce": "",
        "extra": {"type": 1, "guild_id": "g-1", "channel_name": "频道", "author_id": "20001",
                  "author": {"id": "20001", "username": "张三", "identify_num": "1",
                             "online": True, "bot": False, "status": 1, "avatar": "",
                             "vip_avatar": "", "nickname": "张三"},
                  "mention": [], "mention_all": False, "mention_roles": [], "mention_here": False},
        "message": Message("跨适配器你好"),
        "event": {"type": 1, "guild_id": "g-1", "channel_name": "频道", "content": "跨适配器你好",
                  "mention": [], "mention_all": False, "mention_roles": [], "mention_here": False,
                  "author": {"id": "20001", "username": "张三", "identify_num": "1",
                             "online": True, "bot": False, "status": 1, "avatar": "",
                             "vip_avatar": "", "nickname": "张三"}},
    })


def dodo_group(slug):
    from nonebot.adapters.dodo.event import ChannelMessageEvent
    from nonebot.adapters.dodo.models import Member, Personal, TextMessage
    return ChannelMessageEvent(
        event_id="e1", event_type="2001", timestamp=NOW, dodo_source_id="u-1",
        personal=Personal(nick_name="张三", avatar_url="http://a", sex=1),
        message_id="m1", message_type=1, message_body=TextMessage(content="跨适配器你好"),
        member=Member(nick_name="张三", join_time=NOW), island_source_id="isl-1", channel_id="ch-1")


def dodo_private(slug):
    from nonebot.adapters.dodo.event import PersonalMessageEvent
    from nonebot.adapters.dodo.models import Personal, TextMessage
    return PersonalMessageEvent(
        event_id="e2", event_type="1001", timestamp=NOW, dodo_source_id="u-1",
        personal=Personal(nick_name="张三", avatar_url="http://a", sex=1),
        message_id="m2", message_type=1, message_body=TextMessage(content="私聊你好"),
        island_source_id="isl-1")


def kritor_group(slug):
    from nonebot.adapters.kritor.event import GroupMessage
    from nonebot.adapters.kritor.model import GroupSender
    from nonebot.adapters.kritor.protos.kritor.common import Element, TextElement
    return GroupMessage(time=NOW, message_id="m1", message_seq=1, scene=1,
                        elements=[Element(type=1, text=TextElement(text="跨适配器你好"))],
                        group=GroupSender(group_id="11001", uin=20001, nick="张三"),
                        message=None, original_message=None)


def mail_private(slug):
    from nonebot.adapters.mail import NewMailMessageEvent
    from nonebot.adapters.mail.message import Message as MailMessage
    from nonebot.adapters.mail.model import User
    return NewMailMessageEvent(
        id="m1", sender=User(id="u1", name="张三"), subject="主题",
        recipients_to=[User(id="10000", name="bot")], recipients_cc=[], recipients_bcc=[],
        date=None, timezone=0.0, message=MailMessage("私聊你好"),
        original_message=MailMessage("私聊你好"), to_me=False)


def minecraft_private(slug):
    from nonebot.adapters.minecraft import PlayerChatEvent
    from nonebot.adapters.minecraft.message import Message as MCMessage
    from nonebot.adapters.minecraft.models import Player
    return PlayerChatEvent(timestamp=1, post_type="message", event_name="PlayerChatEvent",
                           server_name="srv", server_version="1.20", server_type="vanilla",
                           sub_type="player_chat", player=Player(nickname="Steve", uuid=None),
                           message=MCMessage("私聊你好"))


def wxmp_private(slug):
    cls = event_class("nonebot.adapters.wxmp", "TextMessageEvent")
    return cls(**{"ToUserName": "gh_bot", "FromUserName": "user001", "CreateTime": 1,
                  "MsgType": "text", "MsgId": 1, "Content": "私聊你好"})


def efchat_group(slug):
    from nonebot.adapters.efchat.event import ChannelMessageEvent
    from nonebot.adapters.efchat.message import Message as EFMessage
    return ChannelMessageEvent(cmd="", event_type="message", time=1, nick="张三", head="h",
                               level=105, channel="room-1", message=EFMessage("跨适配器你好"))


def efchat_private(slug):
    from nonebot.adapters.efchat.event import WhisperMessageEvent
    from nonebot.adapters.efchat.message import Message as EFMessage
    return WhisperMessageEvent(cmd="", event_type="whisper", time=1, head="h", level=105,
                               text="私聊你好", message=EFMessage("私聊你好"), **{"from": "zhangsan"})


def yunhu_group(slug):
    cls = event_class("nonebot.adapters.yunhu", "GroupMessageEvent")
    return cls.model_validate({
        "version": "1.0",
        "header": {"eventId": "e1", "eventTime": "1", "eventType": "message.receive.normal"},
        "event": {
            "sender": {"senderId": "20001", "senderType": "user", "senderUserLevel": "member",
                       "senderNickname": "张三", "senderAvatarUrl": "http://a"},
            "chat": {"chatId": "g-1", "chatType": "group"},
            "message": {"msgId": "m1", "sendTime": "1", "chatId": "g-1", "chatType": "group",
                        "contentType": "text", "content": {"text": "跨适配器你好"}},
        },
    })


def bililive_group(slug):
    from nonebot.adapters.bilibili_live import Message as BMessage
    cls = event_class("nonebot.adapters.bilibili_live", "DanmakuEvent")
    # 模型校验器要求网页/开放平台原始结构，这里用 model_construct 直接给字段
    return cls.model_construct(
        room_id=12345, time=1.0, mode=1,
        sender=type("U", (), {"uid": 20001, "name": "张三", "open_id": ""})(),
        content="跨适配器你好", message=BMessage("跨适配器你好"), reply_uname="", reply_mid=0,
        color=1, font_size=25, send_from_me=False, reply_uname_color="", reply_open_id="")


CASES = [
    dict(name="OneBot V11", module="nonebot.adapters.onebot.v11", group=ob11_group,
         private=ob11_private,
         api={"get_group_info": {"group_id": 11001, "group_name": "群"},
              "get_group_member_info": {"user_id": 20001, "nickname": "张三", "card": "名片",
                                        "role": "member", "sex": "male", "join_time": 1},
              "get_group_member_list": [{"user_id": 20001, "nickname": "张三", "card": "名片"}]}),
    dict(name="OneBot V12", module="nonebot.adapters.onebot.v12", group=ob12_group,
         private=ob12_private, extras={"platform": "qq"},
         api={"get_group_info": {"group_id": "11001", "group_name": "群"},
              "get_user_info": {"user_id": "20001", "user_name": "张三"},
              "get_group_member_info": {"user_id": "20001", "user_name": "张三"},
              "get_group_member_list": [{"user_id": "20001", "user_name": "张三"}]}),
    dict(name="Console", module="nonebot.adapters.console", group=console_group,
         private=console_private),
    dict(name="Satori", module="nonebot.adapters.satori", group=satori_group,
         private=satori_private,
         extras={"platform": "qq", "_self_info": type("I", (), {"features": []})()}),
    dict(name="Telegram", module="nonebot.adapters.telegram", group=telegram_group,
         private=telegram_private, fail_api=("get_chat_member", "get_user_profile_photos")),
    dict(name="Discord", module="nonebot.adapters.discord", group=discord_group,
         private=discord_private, api={"get_guild": None, "get_channel": None,
                                       "get_guild_member": None, "get_guild_roles": []}),
    dict(name="QQ", module="nonebot.adapters.qq", group=qq_group, private=qq_private,
         guild=qq_guild, extras={"bot_info": type("B", (), {"id": "10000"})()},
         fail_api=("get_guild", "get_channel")),
    dict(name="Feishu", module="nonebot.adapters.feishu", group=feishu_group,
         private=feishu_private, fail_api=("call_api",)),
    dict(name="Milky", module="nonebot.adapters.milky", group=milky_group,
         private=milky_private),
    dict(name="Mirai", module="nonebot.adapters.mirai", group=mirai_group,
         private=mirai_private, fail_api=("get_member_profile",)),
    dict(name="Kaiheila", module="nonebot.adapters.kaiheila", group=kaiheila_group,
         # uninfo 的 kook fetcher 按属性访问（id_/name/guild_id…），这里给带这些属性的小对象
         api={"user_view": type("U", (), {"id_": "20001", "username": "张三", "nickname": "张三",
                                          "avatar": "", "roles": [], "joined_at": 0})(),
              "channel_view": type("C", (), {"id_": "ch-1", "name": "频道",
                                             "guild_id": "g-1", "is_category": False, "type": 1})(),
              "guild_view": type("G", (), {"id_": "g-1", "name": "公会", "icon": ""})(),
              "guildRole_list": []}),
    dict(name="DoDo", module="nonebot.adapters.dodo", group=dodo_group, private=dodo_private,
         api={"get_island_info": {"island_source_id": "isl-1", "island_name": "岛"},
              "get_channel_info": {"channel_id": "ch-1", "channel_name": "频道"},
              "get_member_role_list": []}),
    dict(name="Kritor", module="nonebot.adapters.kritor", group=kritor_group,
         fail_api=("get_group_info", "get_group_member_info")),
    dict(name="Mail", module="nonebot.adapters.mail", private=mail_private),
    dict(name="Minecraft", module="nonebot.adapters.minecraft", private=minecraft_private),
    dict(name="WXMP", module="nonebot.adapters.wxmp", private=wxmp_private),
    dict(name="EFChat", module="nonebot.adapters.efchat", group=efchat_group,
         private=efchat_private),
    # 需要 Python ≥3.12（3.10 上这两个包导入即失败）
    dict(name="YunHu", module="nonebot.adapters.yunhu", group=yunhu_group,
         fail_api=("get_group_info",)),
    dict(name="bilibili Live", module="nonebot.adapters.bilibili_live", group=bililive_group,
         bot="WebBot"),
]

RESULTS = {}
IMAGES = {}


def record(adapter, feature, value):
    RESULTS.setdefault(adapter, {})[feature] = str(value)[:200]


async def do_session(case, ev, bot):
    info = await S.resolve_session(bot, ev)
    if info is None:
        return "FAIL 无法解析会话"
    expected_keys = {"Satori": "satori:g-1_c-1", "Kaiheila": "kaiheila:g-1_ch-1"}
    if case["name"] in expected_keys and (info.is_private or info.key != expected_keys[case["name"]]):
        return f"FAIL wrong channel session: {info.key} private={info.is_private}"
    return f"OK key={info.key} private={info.is_private} name={info.user_name!r} card={info.card!r}"


async def do_render(case, ev, bot):
    info = await S.resolve_session(bot, ev)
    unimsg = await U.build_unimsg(bot, ev)
    if unimsg is None:
        return "FAIL 无法通用化"

    async def fake_image(seg, data, sticker):
        return "[图片]"
    text, _ = await U.render_incoming(bot, info, unimsg, U.RenderDeps("小助手", fake_image))
    return f"OK text={text!r}"


async def do_roundtrip(case, ev, bot):
    """alconna 导出（文本+@）→ 再解析回来：收发一致性（不需要往事件里塞消息）"""
    out = UniMessage.text("命令") + At("user", "20001") + Text("参数")
    exported = await out.export(bot=None, adapter=case["name"])
    parsed = UniMessage.of(exported, bot=bot)
    info = await S.resolve_session(bot, ev)

    async def fake_image(seg, data, sticker):
        return "[图片]"
    text, _ = await U.render_incoming(bot, info, parsed, U.RenderDeps("小助手", fake_image))
    return f"OK out={str(exported)[:60]!r} back={text!r}"


async def do_send(case, ev, bot):
    info = await S.resolve_session(bot, ev)
    target = getattr(info, "target", None) or ev
    msgs, _ = await U.compose_unimsg_list(bot, info, [{"type": "text", "content": "回复内容"}])
    await U.send_unimsg(bot, target, msgs[0])
    calls = [c[0] for c in bot.calls]
    return f"OK api={calls}"


async def do_at_resolve(case, ev, bot):
    info = await S.resolve_session(bot, ev)
    uid = await S.resolve_at_target(bot, info, "张三")
    return f"OK uid={uid}"


async def do_capture(case, ev, bot):
    info = await S.resolve_session(bot, ev)
    mod._group_chunks.pop(info.key, None)
    mod._bus._buffers.pop(info.key, None)
    payload = await UniMessage.text("插件输出").export(bot=None, adapter=case["name"])
    tok_e = current_event.set(ev)
    tok_m = current_matcher.set(type("M", (), {"plugin_name": "other_plugin"})())
    try:
        for hook in list(NBBot._calling_api_hook):
            await hook(bot, "send_message", {"message": payload})
    finally:
        current_event.reset(tok_e)
        current_matcher.reset(tok_m)
    got = [m.content for m in mod._group_chunks.get(info.key, [])]
    return f"OK buffered={got}"


async def do_invoke(case, ev, bot):
    from nonebot_plugin_alconna import Alconna, Args, MultiVar, on_alconna
    info = await S.resolve_session(bot, ev)
    hits = []

    # One strongly held command/matcher for the whole matrix. Re-registering the
    # same name or losing Alconna's weakly referenced command corrupts the probe.
    if not hasattr(mod, "_adapter_probe_matcher"):
        mod._adapter_probe_command = Alconna("probe_cmd", Args["rest", MultiVar(str, "*")])
        mod._adapter_probe_matcher = on_alconna(mod._adapter_probe_command, priority=50, block=True)
        mod._adapter_probe_hits = []

        @mod._adapter_probe_matcher.handle()
        async def _(event: Event):
            mod._adapter_probe_hits.append(str(event.get_message()))
    hits = mod._adapter_probe_hits
    hits.clear()

    original_text = str(ev.get_message())
    copied = await mod._invoker._build_event(
        bot, info, ev, "probe_cmd", user_id=20001,
        parts=[{"type": "text", "content": "参数"}])
    if copied.get_message().extract_plain_text() != "probe_cmd 参数":
        return f"FAIL copied message still reads original text: {copied.get_message()!r}"
    if str(ev.get_message()) != original_text:
        return "FAIL template event was mutated"
    result = await mod._invoker.invoke(bot, info, ev, "probe_cmd", timeout=3, user_id=20001,
                                       parts=[{"type": "text", "content": "参数"}])
    if result != "ok" or not hits:
        return f"FAIL result={result} hits={hits[-1:] if hits else []}"
    return f"OK result={result} hits={hits[-1:]}"


async def do_tool_send(case, ev, bot):
    from nonebot_plugin_aigf_master.message_sender import MessageSender
    from nonebot_plugin_aigf_master.tool_runtime import BatchContext
    from nonebot_plugin_aigf_master.tool_schemas import SendMessage
    info = await S.resolve_session(bot, ev)
    if info is None:
        return "FAIL 无法解析会话"
    processor = mod._get_processor(info.key)
    processor.config = processor.config.model_copy(deep=True)
    processor.config.aigfm_action_protocol = "tools"
    processor._bot, processor._session, processor._session_event = bot, info, ev
    processor._current_user_id = info.user_id
    context = BatchContext(processor._invocation_batch(), processor._generation, processor, 30)
    sender = MessageSender(processor, context)
    request = SendMessage.model_validate({"messages": [{"key": "adapter_send", "parts": [
        {"type": "text", "content": "工具消息"}, {"type": "at", "target": "20001"}]}]})
    before = len(bot.calls)
    result = await sender.send(request)
    if result["status"] != "sent" or len(bot.calls) <= before:
        return f"FAIL result={result['status']} api_calls={len(bot.calls) - before}"
    if not processor.recent_messages or "工具消息" not in processor.recent_messages[-1].content:
        return "FAIL 成功发送后未记入聊天历史"
    return f"OK status=sent calls={len(bot.calls) - before}"


async def do_private(case, ev, bot):
    if "private" not in case:
        return "N/A 无群/频道消息类型"
    info = await S.resolve_session(bot, case["private"]("x"))
    if info is None:
        return "FAIL 私聊会话解析失败"
    return f"OK key={info.key} private={info.is_private} enabled={S.is_enabled(info, mod.plugin_config)}"


CHECKERS = [("会话解析", do_session), ("消息渲染", do_render), ("导出往返", do_roundtrip),
            ("回复发送", do_send), ("工具发送", do_tool_send), ("私聊", do_private), ("@ 解析", do_at_resolve),
            ("跨插件捕获", do_capture), ("插件调用", do_invoke)]


async def main():
    for case in CASES:
        name = case["name"]
        print(f"\n=== {name}")
        try:
            module = importlib.import_module(case["module"])
            bot_cls = getattr(module, case["bot"]) if "bot" in case else getattr(module, "Bot")
            message_cls = getattr(module, "Message")
        except Exception as e:
            record(name, "装载", f"FAIL {type(e).__name__}: {e}")
            print(f"  装载 FAIL {type(e).__name__}: {e}")
            continue
        record(name, "装载", "OK")
        bot = make_bot(name, bot_cls, message_cls, case.get("extras"), case.get("api"),
                       case.get("fail_api", ()))
        # 群/频道优先，否则用私聊事件作为主事件
        builders = [k for k in ("group", "guild") if k in case] or ["private"]
        for kind in builders:
            try:
                ev = case[kind]("x")
                record(name, "构图事件", "OK")
            except Exception as e:
                record(name, "构图事件", f"FAIL {type(e).__name__}: {str(e)[:120]}")
                print(f"  构图事件 FAIL {type(e).__name__}: {str(e)[:200]}")
                continue
            for check_name, checker in CHECKERS:
                if check_name == "私聊" and kind != "group" and kind != "guild":
                    continue
                key = check_name if kind in ("group", "guild") else f"{check_name}(私聊)"
                try:
                    value = await checker(case, ev, bot)
                except Exception as e:
                    value = f"FAIL {type(e).__name__}: {str(e)[:120]}"
                record(name, key, value)
                print(f"  {check_name:10s} {value[:150]}")
            break


run(main())

(TMP / "adapter_matrix.json").write_text(
    json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")

cols = ["装载", "构图事件"] + [c[0] for c in CHECKERS]
print("\n\n===== 矩阵")
print("适配器".ljust(12) + "".join(c.ljust(13) for c in cols))
for case in CASES:
    row = RESULTS.get(case["name"], {})
    private_only = not any(k in case for k in ("group", "guild"))
    cells = []
    for c in cols:
        v = row.get(c)
        if v is None:
            v = row.get(f"{c}(私聊)" if private_only else c, "-")
        cells.append(("-" if v == "-" else ("OK" if v.startswith("OK") else ("N/A" if v.startswith("N/A") else "FAIL"))).ljust(13))
    print(case["name"].ljust(12) + "".join(cells))

bad = {(a, f): v for a, d in RESULTS.items() for f, v in d.items() if not v.startswith(("OK", "N/A"))}
print(f"\n检查点 {sum(len(d) for d in RESULTS.values())} 个，失败 {len(bad)}")
for (a, f), v in bad.items():
    print(f"  - {a} / {f}: {v[:130]}")
sys.exit(1 if bad else 0)

"""Real Satori/Kaiheila events: eager caches, read-only message slots and segments."""
from _bootstrap import REPO, SOURCE, TMP

from datetime import datetime
from itertools import count
from types import SimpleNamespace
import unittest

import nonebot

nonebot.init(driver="~none", localstore_use_cwd=True, log_level="CRITICAL", command_start={""})
IS_PEER = (SOURCE / "nonebot_plugin_aigfm_peer").is_dir()
PACKAGE = "nonebot_plugin_aigfm_peer" if IS_PEER else "nonebot_plugin_aigf_master"
mod = nonebot.load_plugin(PACKAGE).module
if not IS_PEER:
    from nonebot_plugin_aigf_master.plugin_invoker import PluginInvoker


def bot(name, message_class):
    from importlib import import_module
    module = "satori" if name == "Satori" else "kaiheila"
    native_class = import_module(f"nonebot.adapters.{module}").Bot

    class OfflineBot(native_class):
        def __init__(self):
            pass

        async def call_api(self, api, **kwargs):
            raise NotImplementedError("offline test")

    result = OfflineBot()
    result.self_id = "10000"
    result.platform = "qq"
    result.adapter = SimpleNamespace(get_name=lambda: name,
                                     get_message_class=lambda: message_class,
                                     config=nonebot.get_driver().config)
    result.get_self_id = lambda: "10000"
    return result


_EVENT_IDS = count()

def satori_event(text):
    from nonebot.adapters.satori.event import MessageCreatedEvent
    return MessageCreatedEvent.model_validate({
        "type": "message-created", "timestamp": int(datetime.now().timestamp() * 1000),
        "login": {"sn": 1, "status": 1, "adapter": "satori", "platform": "qq",
                  "user": {"id": "10000", "name": "bot"}},
        "channel": {"id": "channel", "type": 0}, "guild": {"id": "guild"},
        "user": {"id": "111", "name": "user"},
        "message": {"id": f"copy-event-{next(_EVENT_IDS)}", "content": text},
    })


def kaiheila_event(text):
    from nonebot.adapters.kaiheila.event import MessageEvent
    author = {"id": "111", "username": "user", "identify_num": "1", "online": True,
              "bot": False, "status": 1, "avatar": "", "vip_avatar": ""}
    return MessageEvent.model_validate({
        "post_type": "message", "message_type": "group", "sub_type": "group",
        "channel_type": "GROUP", "type": 1, "target_id": "channel", "author_id": "111",
        "user_id": "111", "content": text, "msg_id": f"copy-event-{next(_EVENT_IDS)}",
        "msg_timestamp": 1, "nonce": "",
        "extra": {"type": 1, "guild_id": "guild", "author": author},
        "event": {"type": 1, "guild_id": "guild", "author": author, "content": text},
    })


class CopyTests(unittest.IsolatedAsyncioTestCase):
    async def copy(self, native_bot, event, command, parts=None):
        if IS_PEER:
            return await mod._copy_event(native_bot, event, command, parts=parts)
        return await PluginInvoker._build_event(native_bot, None, event, command, parts=parts)

    async def exercise(self, name, module, factory):
        from importlib import import_module
        message_class = import_module(module).Message
        native_bot = bot(name, message_class)
        for original in ("old text", "command"):
            event = factory(original)
            original_message = str(event.get_message())
            original_id = event.message.id if name == "Satori" else event.msg_id
            copied = await self.copy(native_bot, event, "command", [
                {"type": "at", "target": "222"}, {"type": "text", "content": "argument"}])
            self.assertIn("command", copied.get_message().extract_plain_text())
            self.assertIn("argument", copied.get_message().extract_plain_text())
            self.assertTrue(any(segment.type in ("at", "mention") for segment in copied.get_message()))
            self.assertEqual(copied.get_user_id(), "111")
            self.assertEqual(str(event.get_message()), original_message)
            # In particular, original == command must not corrupt structured slots.
            if name == "Satori":
                self.assertEqual(copied.message.id, original_id)
                self.assertEqual(str(copied.original_message), str(copied.get_message()))
            else:
                self.assertEqual(str(copied.event.content), str(copied.get_message()))
            same = await self.copy(native_bot, event, original)
            self.assertEqual(same.get_message().extract_plain_text(), original)
            if name == "Satori":
                self.assertEqual(same.message.id, original_id)
            again = await self.copy(native_bot, event, "another")
            self.assertEqual(again.get_message().extract_plain_text(), "another")
            self.assertEqual(str(event.get_message()), original_message)

    async def test_copied_commands_reach_alconna_matcher(self):
        from importlib import import_module
        from nonebot.adapters import Event
        from nonebot.message import handle_event
        from nonebot_plugin_alconna import Alconna, Args, MultiVar, on_alconna
        command = Alconna("copy_probe", Args["rest", MultiVar(str, "*")])
        matcher = on_alconna(command, priority=1, block=True)
        hits = []

        @matcher.handle()
        async def receive(event: Event):
            hits.append(event.get_message().extract_plain_text())

        try:
            for name, module, factory in [("Satori", "nonebot.adapters.satori", satori_event),
                                           ("Kaiheila", "nonebot.adapters.kaiheila", kaiheila_event)]:
                native_bot = bot(name, import_module(module).Message)
                copied = await self.copy(native_bot, factory("old"), "copy_probe", [{"type": "text", "content": "argument"}])
                await handle_event(native_bot, copied)
                self.assertEqual(hits[-1:], ["copy_probe argument"], name)
                hits.clear()
        finally:
            matcher.destroy()

    async def test_satori_eager_extra_cache(self):
        await self.exercise("Satori", "nonebot.adapters.satori", satori_event)

    async def test_kaiheila_readonly_message_property(self):
        await self.exercise("Kaiheila", "nonebot.adapters.kaiheila", kaiheila_event)


if __name__ == "__main__":
    unittest.main(verbosity=2)

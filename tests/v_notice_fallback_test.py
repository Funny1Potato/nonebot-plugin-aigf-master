"""Narrow notice fallback must preserve message session keys and reject ambiguity."""
from _bootstrap import REPO, SOURCE, TMP

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init(driver="~none", localstore_use_cwd=True, log_level="CRITICAL")
nonebot.load_plugin("nonebot_plugin_aigf_master")
from nonebot_plugin_aigf_master import session as S


def bot(name):
    return SimpleNamespace(adapter=SimpleNamespace(get_name=lambda: name), self_id="bot")


def event(kind="notice", **fields):
    return SimpleNamespace(get_type=lambda: kind, get_user_id=lambda: "", **fields)


class NoticeFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, name, notice):
        with patch.object(S, "get_session", AsyncMock(return_value=None)), patch.object(
            S, "get_target", side_effect=NotImplementedError
        ):
            return await S.resolve_session(bot(name), notice)

    async def test_explicit_group_ids_map_to_message_keys(self):
        cases = [
            ("Feishu", event(event={"chat_id": "oc-1"}), "feishu:oc-1"),
            ("Feishu", event(event=SimpleNamespace(chat_id="oc-1")), "feishu:oc-1"),
            ("Kritor", event(group_id=11001), "kritor:11001"),
            ("bilibili Live", event(room_id=12345), "bilibililive:12345"),
        ]
        for name, notice, expected in cases:
            info = await self.resolve(name, notice)
            self.assertEqual(info.key, expected)
            self.assertFalse(info.is_private)
            self.assertIsNone(info.target)
            self.assertEqual(info.member_scene_id, info.native_chat_id)

    async def test_no_fallback_for_user_ids_or_guild_only_events(self):
        for name, notice in [
            ("Feishu", event(user_id="user")), ("Kritor", event(group_id=0)),
            ("Kritor", event(group_id=True)), ("Kritor", event(group_id={})),
            ("bilibili Live", event(room_id="")), ("Discord", event(guild_id=10)),
            ("Unknown", event(group_id=1)), ("Kritor", event("message", group_id=1)),
        ]:
            self.assertIsNone(await self.resolve(name, notice))

    async def test_uninfo_result_has_priority(self):
        with patch.object(S, "get_session", AsyncMock(return_value=object())), patch.object(
            S, "_from_uninfo", return_value=SimpleNamespace(user_name="name")
        ) as convert, patch.object(S, "get_target", side_effect=AssertionError("should not fall back")):
            result = await S.resolve_session(bot("Kritor"), event(group_id=1))
        self.assertEqual(result.user_name, "name")
        convert.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

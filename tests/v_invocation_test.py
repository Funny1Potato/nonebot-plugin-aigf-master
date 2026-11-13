"""Delivery certainty, shared quota, retry behavior and request identity regressions."""
from _bootstrap import REPO, SOURCE, TMP

import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import nonebot

nonebot.init(driver="~none", localstore_use_cwd=True, log_level="CRITICAL")
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot_plugin_aigf_master import peer_client as PC, plugin_invoker as PI
from nonebot_plugin_aigf_master.invocation import DeliveryResult
from nonebot_plugin_aigf_master.models import ChatMessage, PluginCommand


class ScriptedLLM:
    multimodal = False

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.receipts = []
        self.before = None
        self.available_tools = []

    async def chat_with_tools(self, prompt, model, tools, handler, **kwargs):
        if self.before:
            await self.before()
        self.available_tools.append({t["function"]["name"] for t in tools})
        actions, response = self.script[self.calls]
        self.calls += 1
        for name, args in actions:
            self.receipts.append(await handler(name, args))
        return response, bool(actions)


LOCAL = ("invoke_plugin", {"command": "first"})
REMOTE = ("invoke_peer_plugin", {"command": "second", "bot": "peer"})
GOOD = '{"reply": []}'


class InvocationTests(unittest.IsolatedAsyncioTestCase):
    def processor(self, script, *, status="dispatched", quota=1):
        proc = mod._get_processor(f"onebot11:{len(mod._processors) + 90000}")
        proc.config = proc.config.model_copy(deep=True)
        proc.config.aigfm_image_mode = "vlm"
        proc.config.aigfm_invoke_max_per_batch = quota
        proc.config.aigfm_search_enabled = True
        proc.config.aigfm_capture_plugins = []
        proc.peer_scanned_commands = {"peer": [{"name": "second"}]}
        proc.social_energy = 1.0
        proc.llm = ScriptedLLM(script)
        proc._bot, proc._session_event = object(), object()
        proc._session = SimpleNamespace(numeric_chat_id=123)
        proc._current_user_id = "111"
        proc.search = SimpleNamespace(search=AsyncMock(return_value=[]), format_results=lambda _: "none")
        proc.invoker = SimpleNamespace(invoke_detailed=AsyncMock(return_value=DeliveryResult(status, status)))
        proc.peer_client = SimpleNamespace(names=lambda: ["peer"], invoke_detailed=AsyncMock(
            return_value=DeliveryResult("accepted", "accepted")))
        return proc

    async def invoke(self, proc, *, batch=None, local=True):
        return await proc._call_llm_with_tools("prompt", [], [object()] if local else [],
                                               [{"name": "second", "bot": "peer"}], batch)

    async def test_shared_quota_blocks_different_commands_and_routes(self):
        for actions in ([LOCAL, REMOTE], [REMOTE, LOCAL], [LOCAL, ("invoke_plugin", {"command": "other"})]):
            proc = self.processor([(actions, GOOD)])
            await self.invoke(proc)
            self.assertEqual(proc.invoker.invoke_detailed.await_count + proc.peer_client.invoke_detailed.await_count, 1)
            self.assertIn("上限", proc.llm.receipts[1])
            self.assertEqual(len(proc._invoke_log), 1)

    async def test_quota_can_be_configured_and_new_batch_resets(self):
        proc = self.processor([([LOCAL, REMOTE], GOOD)], quota=2)
        await self.invoke(proc)
        self.assertEqual(len(proc._invoke_log), 2)
        proc.llm = ScriptedLLM([([("invoke_plugin", {"command": "third"})], GOOD)])
        await self.invoke(proc)
        self.assertEqual(proc.invoker.invoke_detailed.await_count, 2)

    async def test_not_sent_consumes_attempt_but_not_energy_or_dedup(self):
        proc = self.processor([([LOCAL, REMOTE], GOOD)], status="not_sent")
        await self.invoke(proc)
        self.assertEqual(proc._invoke_log, [])
        self.assertEqual(proc.social_energy, 1.0)
        self.assertIn("未投递", proc.recent_messages[0].content)
        self.assertIn("上限", proc.llm.receipts[1])

    async def test_uncertain_is_retained_without_claiming_success(self):
        proc = self.processor([([LOCAL], GOOD)], status="uncertain")
        await self.invoke(proc)
        self.assertEqual(proc._invoke_log[0].status, "uncertain")
        self.assertTrue(proc._invoke_log[0].request_id)
        self.assertAlmostEqual(proc.social_energy, 0.9)
        self.assertIn("不确定", proc._recent_invocations()[0])
        self.assertNotIn("已调用命令", proc.recent_messages[0].content)
        proc.llm = ScriptedLLM([([LOCAL], GOOD)])
        await self.invoke(proc)
        self.assertEqual(proc.invoker.invoke_detailed.await_count, 1)

    async def test_definite_failure_allows_later_batch_retry(self):
        proc = self.processor([([LOCAL], GOOD)], status="not_sent")
        await self.invoke(proc)
        proc.llm = ScriptedLLM([([LOCAL], GOOD)])
        await self.invoke(proc)
        self.assertEqual(proc.invoker.invoke_detailed.await_count, 2)

    async def test_quota_survives_json_parse_retry_in_process(self):
        proc = self.processor([([LOCAL], '{"reply": ['), ([REMOTE], GOOD)])
        with patch.object(proc.learner, "get_all_commands", return_value=[PluginCommand(name="first", plugin_name="other")]):
            await proc.process([ChatMessage(time=datetime.now(), user_name="user", user_id="111", content="do it")])
        self.assertEqual(proc.llm.calls, 2)
        self.assertEqual(proc.invoker.invoke_detailed.await_count, 1)
        self.assertEqual(proc.peer_client.invoke_detailed.await_count, 0)
        self.assertIn("上限", proc.llm.receipts[1])

    async def test_plugin_tools_work_with_search_disabled(self):
        proc = self.processor([([LOCAL], GOOD)])
        proc.config.aigfm_search_enabled = False
        proc.search = None
        with patch.object(proc.learner, "get_all_commands", return_value=[PluginCommand(name="first", plugin_name="other")]):
            await proc.process([ChatMessage(time=datetime.now(), user_name="user", user_id="111", content="do it")])
        proc.invoker.invoke_detailed.assert_awaited_once()

    async def test_image_tool_entry_is_independent_of_search_and_invocation(self):
        proc = self.processor([([], GOOD)])
        proc.config.aigfm_search_enabled = False
        proc.config.aigfm_invoke_enabled = False
        proc.config.aigfm_image_gen_enabled = True
        proc.image_gen = object()
        proc.search = None
        await proc.process([ChatMessage(time=datetime.now(), user_name="user", user_id="111", content="draw")])
        self.assertEqual(proc.llm.available_tools, [{"generate_image"}])

    async def test_peer_failure_does_not_write_success_or_charge_energy(self):
        proc = self.processor([([REMOTE], GOOD)])
        proc.peer_client.invoke_detailed.return_value = DeliveryResult("not_sent", "not sent")
        await self.invoke(proc)
        self.assertEqual(proc._invoke_log, [])
        self.assertEqual(proc.social_energy, 1.0)
        self.assertIn("未投递", proc.recent_messages[0].content)

    async def test_context_survives_new_message_while_llm_waits(self):
        proc = self.processor([([LOCAL], GOOD)])
        old_bot, old_session, old_event = proc._bot, proc._session, proc._session_event

        async def next_message():
            proc._bot, proc._session, proc._session_event = object(), object(), object()
            proc._current_user_id = "222"

        proc.llm.before = next_message
        await self.invoke(proc)
        args, kwargs = proc.invoker.invoke_detailed.call_args
        self.assertEqual(args[:3], (old_bot, old_session, old_event))
        self.assertEqual(kwargs["user_id"], "111")

    async def test_unavailable_tool_and_self_command_do_not_dispatch(self):
        proc = self.processor([([LOCAL], GOOD)])
        await self.invoke(proc, local=False)
        proc.invoker.invoke_detailed.assert_not_awaited()
        proc.llm = ScriptedLLM([([("invoke_plugin", {"command": "reset"})], GOOD)])
        await self.invoke(proc)
        proc.invoker.invoke_detailed.assert_not_awaited()

    async def test_cancellation_keeps_uncertain_attempt_and_propagates(self):
        proc = self.processor([([LOCAL], GOOD)])
        proc.invoker.invoke_detailed.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke(proc)
        self.assertEqual(proc._invoke_log[0].status, "uncertain")
        self.assertAlmostEqual(proc.social_energy, 0.9)

    async def test_client_exception_is_not_reported_as_definite_failure(self):
        proc = self.processor([([LOCAL], GOOD)])
        proc.invoker.invoke_detailed.side_effect = RuntimeError("unexpected")
        await self.invoke(proc)
        self.assertEqual(proc._invoke_log[0].status, "uncertain")
        self.assertIn("不确定", proc.llm.receipts[0])

    async def test_at_resolution_uses_snapshot_and_strips_parameter_edges(self):
        proc = self.processor([])
        batch = proc._invocation_batch()
        proc._bot, proc._session = object(), object()
        with patch("nonebot_plugin_aigf_master.processor.resolve_at_target", AsyncMock(return_value="42")) as resolve:
            parts, skipped = await proc._resolve_parts([
                {"type": "at", "name": "friend"}, {"type": "text", "content": " 10 "}], batch)
        resolve.assert_awaited_once_with(batch.bot, batch.session, "friend")
        self.assertEqual(parts, [{"type": "at", "target": "42"}, {"type": "text", "content": "10"}])
        self.assertEqual(skipped, [])

    async def test_local_delivery_classification_and_legacy_codes(self):
        invoker = PI.PluginInvoker()
        missing = await invoker.invoke_detailed(None, None, None, "x")
        self.assertEqual(missing.status, "not_sent")
        event = object()
        with patch.object(invoker, "_build_event", AsyncMock(return_value=event)):
            with patch.object(PI, "handle_event", AsyncMock(return_value=None)):
                result = await invoker.invoke_detailed(None, None, object(), "x")
                self.assertEqual(result.status, "dispatched")
                self.assertEqual(await invoker.invoke(None, None, object(), "x"), "ok")
            with patch.object(PI, "handle_event", AsyncMock(side_effect=RuntimeError("after side effect"))):
                self.assertEqual((await invoker.invoke_detailed(None, None, object(), "x")).status, "uncertain")
                self.assertEqual(await invoker.invoke(None, None, object(), "x"), "error")
            with patch.object(PI, "handle_event", AsyncMock(side_effect=asyncio.TimeoutError)):
                self.assertEqual(await invoker.invoke(None, None, object(), "x"), "timeout")
        self.assertEqual(invoker._synthetic, {})

    async def test_peer_acknowledgement_classification(self):
        client = PC.PeerClient([{"name": "peer", "port": 1, "token": "test"}])
        self.assertEqual((await client.invoke_detailed("missing", "x", 1)).status, "not_sent")
        original_client = httpx.AsyncClient
        cases = [(200, {"ok": True}, "accepted"), (200, {"ok": False}, "uncertain"),
                 (200, {"error": "bad"}, "uncertain"), (403, {}, "not_sent"),
                 (500, {}, "uncertain"), (408, {}, "uncertain")]
        for code, body, expected in cases:
            transport = httpx.MockTransport(lambda request: httpx.Response(code, json=body))
            with patch.object(PC.httpx, "AsyncClient", lambda **kw: original_client(transport=transport, **kw)):
                self.assertEqual((await client.invoke_detailed("peer", "x", 1)).status, expected)
        for error, expected in [(httpx.ConnectError, "not_sent"), (httpx.ConnectTimeout, "not_sent"),
                                (httpx.ReadTimeout, "uncertain"), (httpx.WriteTimeout, "uncertain")]:
            def fail(request):
                raise error("test", request=request)
            with patch.object(PC.httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(fail), **kw)):
                self.assertEqual((await client.invoke_detailed("peer", "x", 1)).status, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Protocol changes across await boundaries, safe diagnostics and peer correlation."""
from _bootstrap import TMP

import asyncio
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import v_tool_protocol_test as fixture
from nonebot_plugin_aigf_master.operations import ProtocolOverrides, BatchDiagnostics
from nonebot_plugin_aigf_master.peer_client import PeerClient
from nonebot_plugin_aigf_master.tool_runtime import ProcessResult
from nonebot_plugin_aigf_master.config import PluginConfig


class OperationsTests(unittest.IsolatedAsyncioTestCase):
    processor = fixture.ToolTests.processor
    batch = fixture.ToolTests.batch

    async def test_switch_is_persistent_isolated_and_next_batch_only(self):
        proc, other = self.processor(), self.processor()
        store = ProtocolOverrides(TMP / "protocols.json")
        proc.protocol_overrides = other.protocol_overrides = store
        proc.config.aigfm_action_protocol = other.config.aigfm_action_protocol = "legacy_json"
        store.set(proc.session_key, "tools")
        original = proc.memory.load_recent

        async def change_during_load():
            store.set(proc.session_key, "legacy_json")
            self.assertEqual(proc.action_protocol, "tools")
            self.assertEqual(proc.next_protocol, "legacy_json")
            self.assertEqual(other.action_protocol, "legacy_json")
            return await original()

        proc.memory.load_recent = change_during_load
        result, send = await self.batch(proc, [fixture.response(text="ignored")])
        self.assertEqual(result.protocol, "tools")
        send.assert_not_awaited()
        self.assertEqual(ProtocolOverrides(store.path).resolve(proc.session_key, proc.config), "legacy_json")
        proc.llm.chat = AsyncMock(return_value='{"reply": []}')
        result, _ = await self.batch(proc, [])
        self.assertEqual(result.protocol, "legacy_json")
        store.set(proc.session_key, "default")
        proc.config.aigfm_session_protocols = {proc.session_key: "tools"}
        self.assertEqual(proc.next_protocol, "tools")
        self.assertEqual(other.next_protocol, "legacy_json")
        self.assertIsNone(proc._active_protocol)

    async def test_corrupt_override_is_never_overwritten(self):
        path = TMP / "broken-protocol.json"
        path.write_text('{"version": 99, "sessions": {}}', encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            ProtocolOverrides(path).set("onebot11:123", "tools")
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            PluginConfig(aigfm_llm_api_key="test", aigfm_llm_base_url="http://localhost",
                         aigfm_session_protocols={"123": "tools"})

    async def test_diagnostics_include_errors_limits_usage_without_contents(self):
        diagnostics = BatchDiagnostics()
        for _ in range(25):
            diagnostics.record(ProcessResult("tools", "call_limit", 2, 3,
                effects=[{"ok": False, "tool": "send_message", "code": "SEND_UNCERTAIN", "data": {"text": "SECRET"}}],
                usage={"total_tokens": 17}, elapsed=1.23, ignored_texts=1))
        rendered = diagnostics.render()
        self.assertEqual(len(diagnostics.records), 20)
        for text in ("SEND_UNCERTAIN", "20 批", "1.23s", "token 17", "忽略普通文本 1"):
            self.assertIn(text, rendered)
        self.assertNotIn("SECRET", rendered)
        self.assertNotIn("SECRET", str(list(diagnostics.records)))

    async def test_cancel_clears_batch_context(self):
        proc = self.processor()
        proc.process = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await proc.run_batch([])
        self.assertIsNone(proc._active_protocol)
        proc.config.aigfm_action_protocol = "legacy_json"
        self.assertEqual(proc.action_protocol, "legacy_json")

    async def test_peer_output_before_ack_timeout_late_output_and_route_checks(self):
        client = PeerClient([{"name": "peer", "port": 1, "token": "test"}])
        real_client = httpx.AsyncClient
        captured = []

        def transport(request):
            payload = json.loads(request.content)
            captured.append(payload)
            if payload["request_id"] == "early":
                client.observe_output("peer", "onebot11:123", "early")
            return httpx.Response(200, json={"ok": True, "request_id": payload["request_id"]})

        with patch("nonebot_plugin_aigf_master.peer_client.httpx.AsyncClient",
                   side_effect=lambda **kw: real_client(transport=httpx.MockTransport(transport), **kw)):
            for ident in ("early", "late"):
                result = await client.invoke_detailed("peer", "echo", 123, session="onebot11:123", request_id=ident)
                self.assertEqual(result.status, "accepted")
            duplicate = await client.invoke_detailed("peer", "echo", 123, session="onebot11:123", request_id="late")
            self.assertEqual(duplicate.status, "uncertain")
        self.assertEqual(len(captured), 2)
        self.assertEqual(client.requests["early"]["state"], "output_received")
        self.assertFalse(client.observe_output("other", "onebot11:123", "late"))
        self.assertFalse(client.observe_output("peer", "onebot11:124", "late"))
        self.assertFalse(client.observe_output("peer", "onebot11:123", "unknown"))
        client.requests["late"]["deadline"] = time.monotonic() - 1
        self.assertIn("等待输出超时", client.status("onebot11:123"))
        self.assertTrue(client.observe_output("peer", "onebot11:123", "late"))
        self.assertNotIn("等待输出超时", client.status("onebot11:123"))
        self.assertIn("不代表执行完成", client.status("onebot11:123"))
        self.assertNotIn("early", client.status("onebot11:124"))

    async def test_old_peer_and_mismatching_ack(self):
        client = PeerClient([{"name": "peer", "port": 1}])
        real_client = httpx.AsyncClient
        bodies = [{"ok": True}, {"ok": True, "request_id": "wrong"}]
        with patch("nonebot_plugin_aigf_master.peer_client.httpx.AsyncClient",
                   side_effect=lambda **kw: real_client(transport=httpx.MockTransport(
                       lambda request: httpx.Response(200, json=bodies.pop(0))), **kw)):
            old = await client.invoke_detailed("peer", "echo", 123)
            wrong = await client.invoke_detailed("peer", "echo", 123)
        self.assertEqual(old.status, "accepted")
        self.assertEqual(wrong.status, "uncertain")

    async def test_peer_cancel_keeps_unknown_delivery_state(self):
        client = PeerClient([{"name": "peer", "port": 1}])
        real_client = httpx.AsyncClient

        async def transport(request):
            raise asyncio.CancelledError()

        with patch("nonebot_plugin_aigf_master.peer_client.httpx.AsyncClient",
                   side_effect=lambda **kw: real_client(transport=httpx.MockTransport(transport), **kw)):
            with self.assertRaises(asyncio.CancelledError):
                await client.invoke_detailed("peer", "echo", 123, request_id="cancelled")
        self.assertEqual(client.requests["cancelled"]["state"], "uncertain")

    async def test_legacy_token_metrics_use_request_local_scope(self):
        from nonebot_plugin_aigf_master.llm_client import LLMClient, batch_usage
        client = LLMClient("test-key", "http://localhost")
        client._client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7)))
        metrics = {"turns": 0, "usage": {}}
        token = batch_usage.set(metrics)
        try:
            await client._create(model="test")
            await client._create(model="test")
        finally:
            batch_usage.reset(token)
            await client._client.close()
        self.assertEqual(metrics, {"turns": 2, "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}})


if __name__ == "__main__":
    unittest.main()

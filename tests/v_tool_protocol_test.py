"""Tool-only integration: effects, multi-turn execution, migration and failures."""
from _bootstrap import REPO, SOURCE, TMP

import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import nonebot
from pydantic import ValidationError

nonebot.init(driver="~none", localstore_use_cwd=True, log_level="CRITICAL")
mod = nonebot.load_plugin("nonebot_plugin_aigf_master").module

from nonebot_plugin_aigf_master.effect_store import EffectStore
from nonebot_plugin_aigf_master.memory_store import MemoryStore
from nonebot_plugin_aigf_master.message_sender import MessageSender
from nonebot_plugin_aigf_master.models import ChatMessage, MemeEntry, PluginCommand
from nonebot_plugin_aigf_master.session import SessionInfo
from nonebot_plugin_aigf_master.store_coordinator import atomic_json, fingerprint, read_json
from nonebot_plugin_aigf_master.tool_handlers import ToolHandlers
from nonebot_plugin_aigf_master.tool_memory import ToolMemory
from nonebot_plugin_aigf_master.tool_runtime import BatchContext, ToolRuntime, receipt
from nonebot_plugin_aigf_master.tool_schemas import CultureValue, GenerateImage, ManageCommand, QueryMemory, SaveMeme, SendMessage, UpdateMemory


def call(name, args, ident=None, raw=False):
    return SimpleNamespace(id=ident or uuid4().hex,
        function=SimpleNamespace(name=name, arguments=args if raw else json.dumps(args)))


def response(*calls, text=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=list(calls)))])


def text_args(text="hello", key="reply"):
    return {"messages": [{"key": key, "parts": [{"type": "text", "content": text}]}]}


class ToolTests(unittest.IsolatedAsyncioTestCase):
    def processor(self):
        proc = mod._get_processor("onebot11:" + str(100000 + len(mod._processors)))
        proc.config = proc.config.model_copy(deep=True)
        proc.config.aigfm_action_protocol = "tools"
        proc.config.aigfm_image_mode = "vlm"
        proc.config.aigfm_search_enabled = False
        proc.config.aigfm_invoke_enabled = False
        proc.config.aigfm_learn_commands = False
        proc._bot = object()
        proc._session_event = object()
        proc._session = SessionInfo(key=proc.session_key, slug="onebot11", scene_path="123",
                                    native_chat_id="123", user_id="111", target=object())
        proc._current_user_id = "111"
        proc.llm = SimpleNamespace(multimodal=False, _create=AsyncMock())
        return proc

    def context(self, proc):
        return BatchContext(proc._invocation_batch(), proc._generation, proc, proc.config.aigfm_tool_batch_timeout)

    async def batch(self, proc, responses):
        proc.llm._create.side_effect = responses
        send = AsyncMock(return_value=object())
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            result = await proc.run_batch([ChatMessage(time=datetime.now(), user_name="user", user_id="111", content="new message")])
        return result, send

    async def test_final_text_and_old_json_are_not_actions(self):
        proc = self.processor()
        result, send = await self.batch(proc, [response(text='{"reply":["do not send"],"memory":{"short_term":{"add":["wrong"]}}}')])
        self.assertEqual(result.stop_reason, "completed")
        send.assert_not_awaited()
        self.assertEqual(await proc.memory.load_short_term(), [])
        self.assertEqual(len(proc.recent_messages), 1)

    async def test_search_http_failure_is_distinct_from_successful_empty_results(self):
        import httpx
        from nonebot_plugin_aigf_master.search_client import BochaSearchClient
        from nonebot_plugin_aigf_master.tool_schemas import Search

        proc = self.processor()
        proc.search = BochaSearchClient("synthetic-key")
        actions = ToolHandlers(proc, self.context(proc), [], [], None, {"111"})
        real_client = httpx.AsyncClient

        def transport(request):
            status = 403 if json.loads(request.content)["query"] == "denied" else 200
            return httpx.Response(status, json={"data": {"webPages": {"value": []}}})

        with patch("nonebot_plugin_aigf_master.search_client.httpx.AsyncClient",
                   side_effect=lambda **kw: real_client(transport=httpx.MockTransport(transport), **kw)):
            denied = await actions("search_internet", Search(query="denied"))
            empty = await actions("search_internet", Search(query="empty"))
            legacy = await proc.search.search("denied")
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["data"]["http_status"], 403)
        self.assertEqual(denied["code"], "SEARCH_HTTP_ERROR")
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["code"], "SEARCH_RESULTS")
        self.assertIn("HTTP 403", proc.search.format_results(legacy))
        self.assertEqual(legacy, [])

    async def test_capture_hook_does_not_resolve_query_apis(self):
        from nonebot.adapters import Bot
        from nonebot.internal.matcher import current_matcher, current_event
        from nonebot_plugin_aigf_master import api_hooks
        before = set(Bot._calling_api_hook)
        old_whitelist = api_hooks.plugin_config.aigfm_capture_plugins
        api_hooks.plugin_config.aigfm_capture_plugins = []
        try:
            api_hooks.register_hooks(SimpleNamespace(), SimpleNamespace())
            hook = (set(Bot._calling_api_hook) - before).pop()
            matcher = current_matcher.set(SimpleNamespace(plugin_name="external_probe"))
            event = current_event.set(object())
            try:
                with patch.object(api_hooks, "resolve_session", AsyncMock(side_effect=AssertionError("query re-entered session resolution"))) as resolve:
                    await hook(object(), "get_group_member_info", {"group_id": 123, "user_id": 111})
                    resolve.assert_not_awaited()
            finally:
                current_event.reset(event)
                current_matcher.reset(matcher)
        finally:
            api_hooks.plugin_config.aigfm_capture_plugins = old_whitelist
            Bot._calling_api_hook.intersection_update(before)

    async def test_multiturn_query_update_send(self):
        proc = self.processor()

        async def model(**kwargs):
            count = proc.llm._create.await_count
            self.assertNotIn("response_format", kwargs)
            if count == 1:
                return response(call("query_memory", {"target": "short_term"}))
            if count == 2:
                snap = json.loads(kwargs["messages"][-1]["content"])["data"]
                return response(call("update_memory", {"target": "short_term", "expected_revision": snap["revision"],
                    "operations": [{"action": "add", "value": "coffee without sugar"}]}))
            self.assertEqual(json.loads(kwargs["messages"][-1]["content"])["status"], "committed")
            return response(call("send_message", text_args("remembered")))

        result, send = await self.batch(proc, model)
        self.assertEqual(result.turns, 3)
        self.assertEqual(result.stop_reason, "turn_limit")
        self.assertEqual(await proc.memory.load_short_term(), ["coffee without sugar"])
        send.assert_awaited_once()
        self.assertEqual(proc.recent_messages[-1].content, "remembered")
        self.assertEqual(len(await proc.memory.load_recent()), 2)

    async def test_invalid_args_block_prearranged_success_message(self):
        proc = self.processor()
        result, send = await self.batch(proc, [
            response(call("update_memory", {"target": "short_term", "expected_revision": "stale", "operations": [{"action": "add", "value": "x"}]}),
                     call("send_message", text_args("remembered"))),
            response(call("send_message", text_args("could not save", "error"))), response()])
        self.assertEqual(result.effects[0]["status"], "conflict")
        self.assertEqual(result.effects[1]["code"], "DEPENDENCY_RESULT_REQUIRED")
        send.assert_awaited_once()
        self.assertEqual(proc.recent_messages[-1].content, "could not save")

    async def test_malformed_and_unknown_calls_have_results_and_no_effect(self):
        proc = self.processor()
        result, send = await self.batch(proc, [response(call("send_message", "{", raw=True),
              call("unknown", {}), call("send_message", {"messages": "wrong"})), response()])
        self.assertEqual([r["code"] for r in result.effects], ["INVALID_ARGUMENTS", "TOOL_NOT_AVAILABLE", "DEPENDENCY_RESULT_REQUIRED"])
        send.assert_not_awaited()

    async def test_send_replay_keys_and_semantic_duplicates(self):
        proc = self.processor()
        result, send = await self.batch(proc, [response(call("send_message", text_args())),
            response(call("send_message", text_args(key="another"))), response(call("send_message", text_args()))])
        send.assert_awaited_once()
        self.assertEqual(len([m for m in proc.recent_messages if m.user_name == proc.bot_name]), 1)

    async def test_argument_feedback_allows_correction_without_echoing_input(self):
        proc = self.processor()

        async def model(**kwargs):
            if proc.llm._create.await_count == 1:
                return response(call("send_message", {"messages": [{"key": "reply", "parts": [
                    {"type": "text", "content": "private synthetic input", "target": "wrong-field"}]}]}))
            if proc.llm._create.await_count == 2:
                result = json.loads(kwargs["messages"][-1]["content"])
                self.assertEqual(result["code"], "INVALID_ARGUMENTS")
                self.assertTrue(result["data"]["errors"])
                self.assertNotIn("private synthetic input", json.dumps(result))
                return response(call("send_message", text_args("corrected")))
            return response()

        result, send = await self.batch(proc, model)
        self.assertEqual(result.stop_reason, "completed")
        send.assert_awaited_once()
        self.assertEqual(proc.recent_messages[-1].content, "corrected")

    async def test_partial_send_is_not_replayed(self):
        proc = self.processor()
        sender = MessageSender(proc, self.context(proc))
        args = SendMessage.model_validate({"messages": [text_args("one", "one")["messages"][0], text_args("two", "two")["messages"][0]]})
        send = AsyncMock(side_effect=[object(), asyncio.TimeoutError()])
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            first = await sender.send(args)
            second = await sender.send(args)
        self.assertEqual(first["status"], "partial")
        self.assertEqual([x["status"] for x in second["data"]["messages"]], ["sent", "uncertain"])
        self.assertEqual(send.await_count, 2)
        self.assertEqual([m.content for m in proc.recent_messages if m.user_name == proc.bot_name], ["one"])

    async def test_send_cancel_records_uncertainty_and_propagates(self):
        proc = self.processor()
        sender = MessageSender(proc, self.context(proc))
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await sender.send(SendMessage.model_validate(text_args()))
        recovered = EffectStore(sender.store.path.parent, sender.context.batch_id)
        self.assertEqual(recovered.get("send:" + fingerprint("reply") + ":0")["status"], "uncertain")
        self.assertFalse(any(m.user_name == proc.bot_name for m in proc.recent_messages))
        self.assertIn("未确认", proc.recent_messages[-1].content)

    async def test_unknown_send_halts_new_calls_and_background_but_allows_status_replay(self):
        proc = self.processor()
        context = self.context(proc)
        sender = MessageSender(proc, context)
        send = AsyncMock(side_effect=asyncio.TimeoutError)
        first = SendMessage.model_validate(text_args("one", "first"))
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            self.assertEqual((await sender.send(first))["status"], "uncertain")
            self.assertEqual((await sender.send(first))["status"], "uncertain")
            self.assertEqual((await sender.send(SendMessage.model_validate(text_args("two", "second"))))["code"], "SEND_HALTED_AFTER_UNCERTAIN")
            self.assertFalse(sender.reserve_image())
            self.assertEqual((await sender.send_image("job", "eA=="))["code"], "SEND_HALTED_AFTER_UNCERTAIN")
            # A new sender/context for the same durable batch must also stop.
            restored = self.context(proc)
            restored.batch_id = context.batch_id
            recovered = MessageSender(proc, restored)
            self.assertTrue(restored.send_halted)
            self.assertEqual((await recovered.send(SendMessage.model_validate(text_args("three", "third"))))["code"], "SEND_HALTED_AFTER_UNCERTAIN")
        self.assertEqual(send.await_count, 1)
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", AsyncMock()) as fresh_send:
            new_batch = MessageSender(proc, self.context(proc))
            self.assertEqual((await new_batch.send(first))["status"], "sent")
            fresh_send.assert_awaited_once()

    async def test_gateway_without_schema_references_can_correct_object_instead_of_array(self):
        proc = self.processor()

        async def model(**kwargs):
            self.assertNotIn('"$ref"', json.dumps(kwargs["tools"]))
            if proc.llm._create.await_count == 1:
                return response(call("send_message", {"messages": [{"key": "reply", "parts": {
                    "item": {"type": "text", "content": "hello"}}}]}))
            if proc.llm._create.await_count == 2:
                feedback = json.loads(kwargs["messages"][-1]["content"])
                self.assertEqual(feedback["data"]["errors"][0]["path"], ["messages", 0, "parts"])
                self.assertIn("item", feedback["data"]["errors"][0]["message"])
                return response(call("send_message", text_args()))
            return response()

        result, send = await self.batch(proc, model)
        self.assertEqual(result.stop_reason, "completed")
        send.assert_awaited_once()

    async def test_intent_failure_prevents_network(self):
        proc = self.processor()
        sender = MessageSender(proc, self.context(proc))
        send = AsyncMock()
        with patch.object(sender.store, "record", side_effect=OSError("disk")), patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            with self.assertRaises(OSError):
                await sender.send(SendMessage.model_validate(text_args()))
        send.assert_not_awaited()

    async def test_entry_ids_conflicts_and_atomic_validation(self):
        proc = self.processor()
        await proc.memory.save_short_term(["old", "keep"])
        memory = ToolMemory(proc.memory, self.context(proc), {"111"})
        await memory.prepare()
        snap = (await memory.query(QueryMemory(target="short_term")))["data"]
        entry = snap["entries"][0]["entry_id"]
        args = {"target": "short_term", "expected_revision": snap["revision"], "operations": [{"action": "modify", "entry_id": entry, "value": "new"}]}
        result = await memory.update(UpdateMemory.model_validate(args))
        self.assertEqual(result["status"], "committed")
        current = (await memory.query(QueryMemory(target="short_term")))["data"]
        self.assertEqual(current["entries"][0]["entry_id"], entry)
        self.assertEqual((await memory.update(UpdateMemory.model_validate(args)))["status"], "conflict")
        bad = {**args, "expected_revision": current["revision"], "operations": [
            {"action": "add", "value": "must not be written"}, {"action": "delete", "entry_id": "missing"}]}
        self.assertFalse((await memory.update(UpdateMemory.model_validate(bad)))["ok"])
        self.assertEqual(await proc.memory.load_short_term(), ["new", "keep"])
        self.assertTrue((proc.memory._group_dir / "short_term.json.pre-tools.bak").exists())

    async def test_memory_disk_failure_does_not_claim_commit(self):
        proc = self.processor()
        memory = ToolMemory(proc.memory, self.context(proc), set())
        await memory.prepare()
        snap = (await memory.query(QueryMemory(target="long_term")))["data"]
        args = UpdateMemory(target="long_term", expected_revision=snap["revision"], operations=[{"action": "add", "value": "x"}])
        with patch("nonebot_plugin_aigf_master.tool_memory.atomic_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await memory.update(args)
        self.assertEqual(await proc.memory.load_long_term(), [])

    async def test_external_edit_invalidates_old_identity(self):
        proc = self.processor()
        await proc.memory.save_short_term(["a"])
        memory = ToolMemory(proc.memory, self.context(proc), set())
        await memory.prepare()
        snap = (await memory.query(QueryMemory(target="short_term")))["data"]
        path = proc.memory._group_dir / "short_term.json"
        doc = read_json(path)
        doc["items"] = ["externally changed"]
        atomic_json(path, doc)
        fresh = (await memory.query(QueryMemory(target="short_term")))["data"]
        self.assertNotEqual(snap["revision"], fresh["revision"])
        self.assertNotEqual(snap["entries"][0]["entry_id"], fresh["entries"][0]["entry_id"])

    async def test_friend_namespaces_and_unknown_owner(self):
        directory = TMP / uuid4().hex
        first, second = MemoryStore("onebot11_1", directory), MemoryStore("telegram_1", directory)
        await first.update_nickname("111", "QQ user")
        await second.update_nickname("111", "Telegram user")
        self.assertEqual((await first.load_friend("111"))["nickname"], "QQ user")
        self.assertEqual((await second.load_friend("111"))["nickname"], "Telegram user")
        proc = self.processor()
        memory = ToolMemory(first, self.context(proc), {"111"})
        await memory.prepare()
        self.assertFalse((await memory.query(QueryMemory(target="friend_info", owner_id="../other")))["ok"])

    async def test_concurrent_friend_info_and_system_card_do_not_overwrite(self):
        proc = self.processor()
        await proc.memory.update_nickname("111", "user")
        memory = ToolMemory(proc.memory, self.context(proc), {"111"})
        await memory.prepare()
        snap = (await memory.query(QueryMemory(target="friend_info", owner_id="111")))["data"]
        args = UpdateMemory(target="friend_info", owner_id="111", expected_revision=snap["revision"],
                            operations=[{"action": "add", "value": "preference"}])
        results = await asyncio.gather(memory.update(args), proc.memory.update_card("111", "new card"))
        self.assertTrue(results[0]["ok"])
        friend = await proc.memory.load_friend("111")
        self.assertEqual(friend["info"], ["preference"])
        self.assertEqual(friend["cards"][proc.memory.group_id], "new card")

    async def test_command_source_and_commit_failures(self):
        proc = self.processor()
        args = ManageCommand(action="learn", route="current", source="source", name="unit_cmd_" + uuid4().hex,
                             evidence_refs=["m1"])
        result = await proc.learner.manage_tools(args)
        self.assertTrue(result["ok"])
        collision = args.model_copy(update={"source": "other"})
        self.assertEqual((await proc.learner.manage_tools(collision))["code"], "COMMAND_SOURCE_CONFLICT")
        previous = deepcopy(proc.learner._commands)
        with patch("nonebot_plugin_aigf_master.command_learner.atomic_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await proc.learner.manage_tools(args)
        self.assertEqual(proc.learner._commands, previous)

    async def test_call_budget_and_per_response_budget(self):
        proc = self.processor()
        proc.config.aigfm_tool_max_calls = 2
        result, send = await self.batch(proc, [response(*[call("query_memory", {"target": "short_term"}) for _ in range(5)])])
        self.assertEqual(result.stop_reason, "call_limit")
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual([x["code"] for x in result.effects[2:]], ["TOOL_BUDGET_EXCEEDED"] * 3)
        send.assert_not_awaited()

    async def test_wall_clock_limit_and_cancellation(self):
        proc = self.processor()
        proc.config.aigfm_tool_batch_timeout = 0.02
        async def slow(**kwargs):
            await asyncio.sleep(2)
        result, send = await self.batch(proc, slow)
        self.assertEqual(result.stop_reason, "deadline")
        send.assert_not_awaited()

    async def test_reset_while_waiting_prevents_new_effects(self):
        proc = self.processor()
        async def model(**kwargs):
            proc._generation += 1
            return response(call("send_message", text_args()))
        result, send = await self.batch(proc, model)
        send.assert_not_awaited()
        self.assertEqual(result.stop_reason, "reset")

    async def test_distinct_mentions_are_not_deduplicated_as_plain_text(self):
        proc = self.processor()
        sender = MessageSender(proc, self.context(proc))
        args = SendMessage.model_validate({"messages": [
            {"key": "a", "parts": [{"type": "at", "target": "111"}]},
            {"key": "b", "parts": [{"type": "at", "target": "222"}]}]})
        send = AsyncMock()
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            result = await sender.send(args)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(send.await_count, 2)
        self.assertEqual([m.content for m in proc.recent_messages], ["@111", "@222"])

    async def test_memes_are_distinct_and_counted_after_send(self):
        from nonebot_plugin_aigf_master.meme_store import MemeStore
        proc = self.processor()
        proc.memes = MemeStore(TMP / uuid4().hex, TMP / uuid4().hex)
        for mid in ("a", "b"):
            path = proc.memes._data_dir / (mid + ".png")
            path.write_bytes(b"test")
            proc.memes._collected_memes[mid] = MemeEntry(mid, path.name)
        sender = MessageSender(proc, self.context(proc))
        args = SendMessage.model_validate({"messages": [
            {"key": "a", "parts": [{"type": "meme", "id": "a"}]},
            {"key": "b", "parts": [{"type": "meme", "id": "b"}]}]})
        send = AsyncMock()
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            result = await sender.send(args)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(send.await_count, 2)
        self.assertEqual([m.usage_count for m in proc.memes.all_memes.values()], [1, 1])

    async def test_invalid_meme_preflight_sends_nothing(self):
        proc = self.processor()
        sender = MessageSender(proc, self.context(proc))
        args = SendMessage.model_validate({"messages": [text_args()["messages"][0],
            {"key": "bad", "parts": [{"type": "meme", "id": "missing"}]}]})
        send = AsyncMock()
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            self.assertFalse((await sender.send(args))["ok"])
        send.assert_not_awaited()
        self.assertEqual(sender.context.sends, 0)

    async def test_corrupt_memory_preserved_while_send_still_works(self):
        proc = self.processor()
        path = proc.memory._group_dir / "short_term.json"
        path.write_text("{broken", encoding="utf-8")
        result, send = await self.batch(proc, [response(call("send_message", text_args())), response()])
        send.assert_awaited_once()
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    async def test_culture_and_alias_tools(self):
        proc = self.processor()
        memory = ToolMemory(proc.memory, self.context(proc), {"111"})
        await memory.prepare()
        for target, owner, value in [("culture", None, {"term": "test", "meaning": "example"}),
                                     ("friend_aliases", "111", "alias")]:
            snap = (await memory.query(QueryMemory(target=target, owner_id=owner)))["data"]
            args = UpdateMemory(target=target, owner_id=owner, expected_revision=snap["revision"],
                                operations=[{"action": "add", "value": value}])
            self.assertTrue((await memory.update(args))["ok"])
        self.assertEqual((await proc.memory.load_culture())[0]["term"], "test")
        self.assertEqual((await proc.memory.load_friend("111"))["aliases"], ["alias"])

    async def test_learning_observation_cannot_inflate_confidence(self):
        proc = self.processor()
        ctx = self.context(proc)
        name = "observed_" + uuid4().hex
        ctx.evidence = {"m1": ChatMessage(datetime.now(), "source", name),
                        "m2": ChatMessage(datetime.now(), "source", name)}
        handlers = ToolHandlers(proc, ctx, [], [], AsyncMock(), set())
        args = ManageCommand(action="learn", route="current", source="source", name=name, evidence_refs=["m1"])
        with patch("nonebot_plugin_aigf_master.plugin_discovery.discover_commands", return_value=[PluginCommand(name, plugin_name="source")]):
            self.assertTrue((await handlers("manage_command", args))["ok"])
            again = args.model_copy(update={"evidence_refs": ["m2"]})
            self.assertEqual((await handlers("manage_command", again))["status"], "unchanged")
        self.assertEqual(proc.learner._commands[name]["confidence"], 1)
        bogus = args.model_copy(update={"evidence_refs": ["history"]})
        self.assertFalse((await handlers("manage_command", bogus))["ok"])

    async def test_background_generation_uses_reserved_original_session(self):
        import base64
        proc = self.processor()
        ctx = self.context(proc)
        handlers = ToolHandlers(proc, ctx, [], [], AsyncMock(), set())
        proc.image_handler = None
        proc.image_gen = SimpleNamespace(generate=AsyncMock(return_value={"b64": base64.b64encode(b"image").decode()}))
        send = AsyncMock()
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            result = await handlers("generate_image", GenerateImage(prompt="test"))
            tasks = list(proc._image_gen_tasks)
            old_target = ctx.invocation.session.target
            proc._session = None
            proc._bot = None
            await asyncio.gather(*tasks)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(ctx.sends, 1)
        send.assert_awaited_once()
        self.assertIs(send.call_args.args[1], old_target)
        self.assertEqual(send.call_args.args[2][0].raw_bytes, b"image")
        self.assertEqual(ctx.generation_jobs[result["data"]["job_id"]], "sent")

    async def test_reset_discards_accepted_background_publication(self):
        import base64
        proc = self.processor()
        ctx = self.context(proc)
        handlers = ToolHandlers(proc, ctx, [], [], AsyncMock(), set())
        proc.image_handler = None
        async def generate(*args, **kwargs):
            proc._generation += 1
            return {"b64": base64.b64encode(b"image").decode()}
        proc.image_gen = SimpleNamespace(generate=generate)
        send = AsyncMock()
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            await handlers("generate_image", GenerateImage(prompt="test"))
            await asyncio.gather(*list(proc._image_gen_tasks))
        send.assert_not_awaited()
        self.assertIn("cancelled", ctx.generation_jobs.values())

    async def test_plugin_quota_is_shared_across_model_turns(self):
        from nonebot_plugin_aigf_master.invocation import DeliveryResult
        proc = self.processor()
        proc.config.aigfm_invoke_enabled = True
        proc.config.aigfm_capture_plugins = []
        proc.invoker = SimpleNamespace(invoke_detailed=AsyncMock(return_value=DeliveryResult("dispatched", "queued")))
        commands = [PluginCommand("first", plugin_name="source"), PluginCommand("second", plugin_name="source")]
        with patch.object(proc.learner, "get_all_commands", return_value=commands):
            result, send = await self.batch(proc, [response(call("invoke_plugin", {"command": "first"})),
                  response(call("invoke_plugin", {"command": "second"})), response()])
        proc.invoker.invoke_detailed.assert_awaited_once()
        self.assertEqual(result.effects[0]["status"], "dispatched")
        self.assertEqual(result.effects[1]["status"], "not_sent")
        self.assertEqual(len(proc._invoke_log), 1)
        send.assert_not_awaited()

    async def test_peer_and_local_share_quota_in_tool_protocol(self):
        from nonebot_plugin_aigf_master.invocation import DeliveryResult
        proc = self.processor()
        proc.config.aigfm_invoke_enabled = True
        proc.config.aigfm_capture_plugins = []
        proc.invoker = SimpleNamespace(invoke_detailed=AsyncMock())
        proc.peer_client = SimpleNamespace(names=lambda: ["peer"], invoke_detailed=AsyncMock(return_value=DeliveryResult("accepted", "accepted")))
        proc.peer_scanned_commands = {"peer": [{"name": "remote"}]}
        with patch.object(proc.learner, "get_all_commands", return_value=[PluginCommand("local", plugin_name="source")]):
            result, send = await self.batch(proc, [response(call("invoke_peer_plugin", {"command": "remote", "bot": "peer"})),
                   response(call("invoke_plugin", {"command": "local"})), response()])
        proc.peer_client.invoke_detailed.assert_awaited_once()
        proc.invoker.invoke_detailed.assert_not_awaited()
        self.assertEqual(result.effects[0]["status"], "accepted")
        self.assertEqual(result.effects[1]["status"], "not_sent")

    async def test_save_meme_is_committed_once_and_session_scoped(self):
        from nonebot_plugin_aigf_master.meme_store import MemeStore
        import base64
        proc = self.processor()
        proc.memes = MemeStore(TMP / uuid4().hex, TMP / uuid4().hex)
        ctx = self.context(proc)
        handlers = ToolHandlers(proc, ctx, [], [], AsyncMock(), set())
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7X8AAAAASUVORK5CYII=")
        mid = await proc.memes.save_to_cache(proc.session_key, image, "image", "happy")
        args = SaveMeme(cache_id=mid, description="happy sticker", keywords=["happy"])
        self.assertEqual((await handlers("save_meme", args))["status"], "saved")
        self.assertEqual((await handlers("save_meme", args))["status"], "already_exists")
        other = self.processor()
        other.memes = proc.memes
        wrong = ToolHandlers(other, self.context(other), [], [], AsyncMock(), set())
        self.assertFalse((await wrong("save_meme", args))["ok"])
        self.assertTrue((proc.memes._data_dir / "collected.json").is_file())

    async def test_disabled_features_are_not_advertised(self):
        proc = self.processor()
        proc.config.aigfm_tool_memory_enabled = False
        proc.config.aigfm_meme_enabled = False
        ctx = self.context(proc)
        handlers = ToolHandlers(proc, ctx, [], [], AsyncMock(), set())
        self.assertEqual([tool["function"]["name"] for tool in handlers.tools()], ["send_message"])

    async def test_sending_budget_is_based_on_actual_split_units(self):
        from nonebot_plugin_aigf_master.meme_store import MemeStore
        proc = self.processor()
        proc.config.aigfm_tool_send_max_messages = 2
        proc.memes = MemeStore(TMP / uuid4().hex, TMP / uuid4().hex)
        path = proc.memes._data_dir / "m.png"
        path.write_bytes(b"test")
        proc.memes._collected_memes["m"] = MemeEntry("m", path.name)
        sender = MessageSender(proc, self.context(proc))
        args = SendMessage.model_validate({"messages": [{"key": "mixed", "parts": [
            {"type": "text", "content": "before"}, {"type": "meme", "id": "m"},
            {"type": "text", "content": "after"}]}]})
        send = AsyncMock()
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            result = await sender.send(args)
        self.assertEqual(result["code"], "SEND_BUDGET_EXCEEDED")
        send.assert_not_awaited()
        self.assertEqual(proc.memes.all_memes["m"].usage_count, 0)

    async def test_memory_only_turn_does_not_require_a_reply(self):
        proc = self.processor()
        async def model(**kwargs):
            if proc.llm._create.await_count == 1:
                doc = read_json(proc.memory._group_dir / "short_term.json")
                rev = doc["_aigfm_meta"]["collections"]["items"]["revision"]
                return response(call("update_memory", {"target": "short_term", "expected_revision": rev,
                    "operations": [{"action": "add", "value": "silent memory"}]}))
            return response()
        result, send = await self.batch(proc, model)
        self.assertEqual(result.stop_reason, "completed")
        send.assert_not_awaited()
        self.assertEqual(await proc.memory.load_short_term(), ["silent memory"])

    async def test_durable_recovery_does_not_replay_or_change_key_contents(self):
        proc = self.processor()
        context = self.context(proc)
        sender = MessageSender(proc, context)
        send = AsyncMock()
        args = SendMessage.model_validate(text_args())
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            await sender.send(args)
            recovered_context = self.context(proc)
            recovered_context.batch_id = context.batch_id
            recovered = MessageSender(proc, recovered_context)
            self.assertEqual((await recovered.send(args))["status"], "sent")
            self.assertEqual((await recovered.send(SendMessage.model_validate(text_args("different"))))["code"], "MESSAGE_KEY_CONFLICT")
            self.assertEqual((await recovered.send(SendMessage.model_validate(text_args(key="new-key"))))["status"], "sent")
        send.assert_awaited_once()
        self.assertEqual(recovered_context.sends, 1)

    async def test_recovery_notes_are_session_scoped(self):
        directory = TMP / uuid4().hex
        store = EffectStore(directory, "old-batch", "onebot11:one")
        store.record("unit", "intent", "digest", kind="send")
        self.assertEqual(len(EffectStore.recovery_notes(directory, "onebot11:one")), 1)
        self.assertEqual(EffectStore.recovery_notes(directory, "onebot11:two"), [])
        self.assertEqual(EffectStore.recovery_notes(directory, "onebot11:one", since=10**12), [])

    async def test_new_input_is_not_duplicated_in_history(self):
        proc = self.processor()
        await self.batch(proc, [response()])
        prompt = proc.llm._create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(prompt.count("new message"), 1)
        self.assertNotIn("请输出 JSON", prompt)
        self.assertNotIn('"reply": []', prompt)

    async def test_migration_inventory_is_read_only(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("preview_migration", REPO / "tools" / "preview_tool_migration.py")
        inventory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inventory)
        folder = TMP / uuid4().hex
        path = folder / "memory" / "friends" / "111.json"
        atomic_json(path, {"id": "111", "groups": ["onebot11_1", "telegram_2"], "info": ["private fact"]})
        before = path.read_bytes()
        result = inventory.inventory(folder)
        self.assertEqual(result["ambiguous_friends"], 1)
        self.assertEqual(result["metadata_pending"], 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("private fact", json.dumps(result))

    async def test_repeated_call_ids_are_bounded_and_content_checked(self):
        proc = self.processor()
        result, send = await self.batch(proc, [response(call("send_message", text_args(), "same")),
            response(call("send_message", json.dumps(text_args(), indent=2), "same", raw=True)),
            response(call("send_message", text_args("changed"), "same"))])
        send.assert_awaited_once()
        self.assertEqual(result.tool_calls, 3)
        self.assertEqual(result.effects[-1]["code"], "TOOL_CALL_ID_CONFLICT")

    async def test_inflight_image_job_is_not_duplicated_by_new_batch(self):
        import base64
        proc = self.processor()
        proc.image_handler = None
        gate = asyncio.Event()
        async def generate(*args, **kwargs):
            await gate.wait()
            return {"b64": base64.b64encode(b"image").decode()}
        generate_mock = AsyncMock(side_effect=generate)
        proc.image_gen = SimpleNamespace(generate=generate_mock)
        first = ToolHandlers(proc, self.context(proc), [], [], AsyncMock(), set())
        second = ToolHandlers(proc, self.context(proc), [], [], AsyncMock(), set())
        send = AsyncMock()
        with patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            a = await first("generate_image", GenerateImage(prompt="same job"))
            b = await second("generate_image", GenerateImage(prompt="same job"))
            self.assertEqual(a["data"]["job_id"], b["data"]["job_id"])
            self.assertEqual(second.context.sends, 0)
            gate.set()
            await asyncio.gather(*list(proc._image_gen_tasks))
        generate_mock.assert_awaited_once()
        send.assert_awaited_once()

    async def test_meme_index_failure_does_not_report_saved(self):
        from nonebot_plugin_aigf_master.meme_store import MemeStore
        import base64
        proc = self.processor()
        proc.memes = MemeStore(TMP / uuid4().hex, TMP / uuid4().hex)
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7X8AAAAASUVORK5CYII=")
        mid = await proc.memes.save_to_cache(proc.session_key, image, "sticker", "happy")
        actions = ToolHandlers(proc, self.context(proc), [], [], AsyncMock(), set())
        with patch("nonebot_plugin_aigf_master.meme_store.atomic_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await actions("save_meme", SaveMeme(cache_id=mid, description="sticker", keywords=["happy"]))
        self.assertNotIn(mid, proc.memes.all_memes)
        self.assertFalse((proc.memes._data_dir / "collected.json").exists())

    async def test_lost_delivery_record_is_uncertain_and_not_replayed(self):
        proc = self.processor()
        sender = MessageSender(proc, self.context(proc))
        record = sender.store.record
        count = 0
        def fail_ack(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError("ack disk write failed")
            return record(*args, **kwargs)
        send = AsyncMock()
        with patch.object(sender.store, "record", side_effect=fail_ack), patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", send):
            self.assertEqual((await sender.send(SendMessage.model_validate(text_args())))["status"], "uncertain")
            self.assertEqual((await sender.send(SendMessage.model_validate(text_args())))["status"], "uncertain")
        send.assert_awaited_once()
        self.assertFalse(any(m.user_name == proc.bot_name for m in proc.recent_messages))


if __name__ == "__main__":
    unittest.main(verbosity=2)

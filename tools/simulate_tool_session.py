"""Simulate full NoneBot ingress/batching/native sends with isolated data.

--offline uses scripted model responses. --live uses only selected configured
model/proxy fields. Neither mode connects a chat platform or starts a driver.
"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--offline", action="store_true")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--resume", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    harness_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    source = (args.source or repo).resolve()
    allowed = {"aigfm_llm_api_key", "aigfm_llm_base_url", "aigfm_llm_model",
               "aigfm_llm_temperature", "aigfm_proxy_enabled", "aigfm_http_proxy", "aigfm_https_proxy"}
    if args.resume:
        settings = json.loads(sys.stdin.read())
        if not isinstance(settings, dict) or set(settings) - allowed:
            parser.error("Invalid private transport settings")
    elif args.live:
        if not args.workspace or not args.env_file:
            parser.error("--live requires --workspace and --env-file")
        from dotenv import dotenv_values
        workspace = args.workspace.resolve()
        env_file = args.env_file if args.env_file.is_absolute() else workspace / args.env_file
        if not env_file.is_file():
            parser.error("Environment file does not exist")
        values = {k.lower(): v for k, v in {
            **dotenv_values(workspace / ".env"), **dotenv_values(env_file),
            **{k: v for k, v in os.environ.items() if k.upper().startswith("AIGFM_")},
        }.items()}
        settings = {k: v for k, v in values.items() if k in allowed and v is not None}
        if not all(settings.get(k) for k in ("aigfm_llm_api_key", "aigfm_llm_base_url", "aigfm_llm_model")):
            parser.error("Explicit model credentials and endpoint are required")
    else:
        settings = {"aigfm_llm_api_key": "test-key", "aigfm_llm_base_url": "http://127.0.0.1:9/v1",
                    "aigfm_llm_model": "scripted"}
    results = repo / "tests/.results"
    results.mkdir(parents=True, exist_ok=True)
    if args.resume:
        run = args.resume.resolve()
        if run.parent != results.resolve() or not run.name.startswith("simulated-session-") or not (run / "simulation.json").is_file():
            parser.error("Resume requires an existing isolated simulation directory")
    else:
        run = Path(tempfile.mkdtemp(prefix="simulated-session-", dir=results))
        (run / "simulation.json").write_text('{"synthetic_data_only":true}', encoding="utf-8")
    for key in list(os.environ):
        if key.upper().startswith(("AIGFM_", "LOCALSTORE_")):
            del os.environ[key]
    os.chdir(run)
    sys.path.insert(0, str(source))
    import nonebot
    nonebot.init(driver="~none", localstore_use_cwd=True, uninfo_cache=False, log_level="CRITICAL",
                 command_start={""}, superusers={"990999"}, aigfm_action_protocol="tools",
                 aigfm_enabled_groups=[990101, 990102], aigfm_batch_count=2,
                 aigfm_batch_timeout=0.1, aigfm_incomplete_timeout=0.1, aigfm_merge_window=0.05,
                 aigfm_vlm_enabled=False, aigfm_anime_backend="off", aigfm_image_mode="vlm",
                 aigfm_search_enabled=False, aigfm_invoke_enabled=False, aigfm_learn_commands=False,
                 **settings)
    loaded = nonebot.load_plugin("nonebot_plugin_aigf_master")
    if loaded is None:
        raise RuntimeError("plugin_load_failed")
    mod = loaded.module
    from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
    from nonebot.message import handle_event
    from nonebot_plugin_aigf_master.store_coordinator import read_json

    sequence = itertools.count(100000)
    outputs, checks, requests, tasks = [], {}, [], []
    first_started, release_first = asyncio.Event(), asyncio.Event()

    class Adapter:
        config = nonebot.get_driver().config

        def get_name(self):
            return "OneBot V11"

        def get_message_class(self):
            return Message

        async def _call_api(self, bot, api, **data):
            raise RuntimeError("unexpected_adapter_transport")

    class SimBot(Bot):
        async def call_api(self, api, **data):
            if api.startswith("send_"):
                if data.get("group_id") not in (990101, 990102):
                    raise RuntimeError("send_outside_simulation")
                outputs.append({"group": data["group_id"], "message": Message(data.get("message", ""))})
                return {"message_id": next(sequence)}
            if api == "get_login_info":
                return {"user_id": 990000, "nickname": "模拟机器人"}
            if api == "get_group_info":
                return {"group_id": data["group_id"], "group_name": "模拟测试群"}
            if api in ("get_group_member_info", "get_stranger_info"):
                return {"user_id": data.get("user_id", 990001), "nickname": "模拟用户", "card": "",
                        "role": "member", "sex": "unknown", "join_time": 1}
            if api == "get_group_member_list":
                return [{"user_id": uid, "nickname": "模拟用户", "card": "", "role": "member"}
                        for uid in (990001, 990002, 990999)]
            raise RuntimeError("unexpected_platform_api")

    bot = SimBot(adapter=Adapter(), self_id="990000")

    def event(text, group=990101, user=990001, mention=True):
        message = Message([MessageSegment.at(990000), MessageSegment.text(text)]) if mention else Message(text)
        return GroupMessageEvent(time=int(datetime.now().timestamp()), self_id=990000, post_type="message",
            sub_type="normal", message_type="group", group_id=group, user_id=user,
            message_id=next(sequence), message=message, raw_message=str(message), font=0,
            sender={"user_id": user, "nickname": "模拟用户", "role": "admin"})

    def reply(*calls):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=list(calls)))])

    def call(name, payload):
        return SimpleNamespace(id="sim_" + str(next(sequence)),
            function=SimpleNamespace(name=name, arguments=json.dumps(payload, ensure_ascii=False)))

    class Model:
        multimodal = False

        def __init__(self, proc):
            self.proc, self.stage, self.turn = proc, "", 0

        async def _create(self, **kwargs):
            self.turn += 1
            if self.stage == "add" and self.turn == 1:
                first_started.set()
                await release_first.wait()
            if args.live:
                response = await mod._llm._create(**kwargs)
            elif self.stage in ("add", "modify") and self.turn == 1:
                doc = read_json(self.proc.memory._group_dir / "long_term.json")
                meta = doc["_aigfm_meta"]["collections"]["facts"]
                operation = {"action": "add", "value": "模拟项目的主题色是绿色。"}
                if self.stage == "modify":
                    operation = {"action": "modify", "entry_id": meta["ids"][0], "value": "模拟项目的主题色是紫色。"}
                response = reply(call("update_memory", {"target": "long_term", "expected_revision": meta["revision"], "operations": [operation]}))
            elif (self.stage in ("add", "modify") and self.turn == 2) or (self.stage in ("chat", "recall", "after_reset") and self.turn == 1):
                text = {"add": "SIM_A_SAVED", "modify": "SIM_A_CHANGED", "chat": "SIM_B_HELLO",
                        "recall": "紫色", "after_reset": "SIM_A_AFTER_RESET"}[self.stage]
                parts = [{"type": "text", "content": text}]
                if self.stage == "add":
                    parts.insert(0, {"type": "at", "target": "990001"})
                response = reply(call("send_message", {"messages": [{"key": self.stage, "parts": parts}]}))
            else:
                response = reply()
            usage = getattr(response, "usage", None)
            requests.append({"session": "A" if self.proc.session_key.endswith("990101") else "B",
                "stage": self.stage, "turn": self.turn,
                "tools": [c.function.name for c in response.choices[0].message.tool_calls or []],
                "total_tokens": getattr(usage, "total_tokens", None)})
            return response

    def processor(group, stage):
        proc = mod._get_processor("onebot11:" + str(group))
        if not isinstance(proc.llm, Model):
            proc.llm = Model(proc)
        proc.llm.stage, proc.llm.turn = stage, 0
        return proc

    async def start_loops():
        for group in (990101, 990102):
            tasks.append(asyncio.create_task(mod._batch_processor("onebot11:" + str(group))))

    async def stop_loops():
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()

    async def finished(proc, previous=None):
        async def wait():
            while proc.last_process_result is None or proc.last_process_result is previous:
                await asyncio.sleep(0.05)
            return proc.last_process_result
        return await asyncio.wait_for(wait(), timeout=190)

    async def step(proc, text, group=990101):
        previous = proc.last_process_result
        await handle_event(bot, event(text, group=group))
        result = await finished(proc, previous)
        return result

    def texts(group):
        return "\n".join(o["message"].extract_plain_text() for o in outputs if o["group"] == group)

    async def flow():
        try:
            if args.resume:
                proc = processor(990101, "recall")
                before = await proc.memory.load_recent()
                checks["fresh_process_starts_without_history_cache"] = not proc.recent_messages and bool(before)
                await start_loops()
                await step(proc, "请根据长期记忆回复模拟项目当前主题色，只回复颜色，不要改记忆。")
                checks["restored_color_from_disk"] = "紫色" in texts(990101)
                checks["restored_chat_history"] = proc.recent_messages[:len(before)] == before
                return
            a, b = processor(990101, "add"), processor(990102, "chat")
            await a.memory.save_short_term(["模拟旧短期记录"])
            initial_short = (a.memory._group_dir / "short_term.json").read_bytes()
            shutil.copytree(mod._data_dir, run / "baseline-backup")
            await start_loops()
            await handle_event(bot, event("禁用会话消息", group=990199))
            checks["disabled_session_rejected"] = "onebot11:990199" not in mod._group_chunks
            await handle_event(bot, event("请在长期记忆中记录模拟项目主题色，下一条给具体内容。"))
            await handle_event(bot, event("模拟项目的主题色是绿色。保存成功后先 @我，再回复 SIM_A_SAVED。"))
            await asyncio.wait_for(first_started.wait(), timeout=15)
            await handle_event(bot, event("请只回复 SIM_B_HELLO，不保存记忆。", group=990102, user=990002))
            release_first.set()
            ar, br = await asyncio.gather(finished(a), finished(b))
            checks["two_messages_batched"] = sum("模拟项目" in m.content for m in a.recent_messages if m.user_id) == 2
            checks["memory_committed"] = any(e["tool"] == "update_memory" and e["status"] == "committed" for e in ar.effects)
            checks["native_at_original_user"] = any(seg.type == "at" and str(seg.data.get("qq")) == "990001"
                for out in outputs if out["group"] == 990101 for seg in out["message"])
            checks["overlapping_sessions_keep_targets"] = "SIM_A_SAVED" in texts(990101) and "SIM_B_HELLO" in texts(990102) and "SIM_B_HELLO" not in texts(990101) and "SIM_A_SAVED" not in texts(990102)
            checks["other_session_memory_unchanged"] = not await b.memory.load_long_term()
            checks["migration_backup_preserved"] = (a.memory._group_dir / "short_term.json.pre-tools.bak").read_bytes() == initial_short
            doc = read_json(a.memory._group_dir / "long_term.json")
            before_ids = doc["_aigfm_meta"]["collections"]["facts"]["ids"]
            a = processor(990101, "modify")
            await step(a, "把长期记忆里模拟项目的主题色从绿色改成紫色，修改原条目不要新增。成功后回复 SIM_A_CHANGED。")
            doc = read_json(a.memory._group_dir / "long_term.json")
            checks["correction_keeps_entry_identity"] = doc["_aigfm_meta"]["collections"]["facts"]["ids"] == before_ids and len(doc["facts"]) == 1 and "紫色" in doc["facts"][0]
            await stop_loops()
            command = [sys.executable, str(Path(__file__).resolve()), "--live" if args.live else "--offline",
                       "--source", str(source), "--resume", str(run)]
            child = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                await asyncio.wait_for(child.communicate(json.dumps(settings).encode()), timeout=210)
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.communicate()
            restored = read_json(run / "resume-report.json")
            checks["fresh_process_recovery"] = child.returncode == 0 and restored.get("harness_sha256") == harness_hash
            generation = a._generation
            await handle_event(bot, event("reset", user=990999, mention=False))
            checks["reset_clears_disk_and_memory_history"] = a._generation == generation + 1 and not a.recent_messages and not (a.memory._group_dir / "recent.json").exists()
            checks["reset_retains_committed_memory"] = any("紫色" in v for v in await a.memory.load_long_term())
            a = processor(990101, "after_reset")
            await start_loops()
            await step(a, "请只回复 SIM_A_AFTER_RESET，不修改记忆。")
            checks["reset_history_not_reimported"] = not any("SIM_A_CHANGED" in m.content for m in a.recent_messages) and "SIM_A_AFTER_RESET" in texts(990101)
        finally:
            release_first.set()
            await stop_loops()
            await mod._llm._client.close()

    error = None
    try:
        asyncio.run(flow())
    except Exception as exc:
        error = type(exc).__name__
    report = {"live_model": args.live, "real_platform": False, "synthetic_data_only": True,
              "fresh_process": bool(args.resume), "checks": checks,
              "passed": bool(checks) and all(checks.values()) and error is None,
              "error_type": error, "platform_sends": len(outputs), "requests": requests,
              "harness_sha256": harness_hash,
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (source / "nonebot_plugin_aigf_master").glob("*.py")}}
    output = run / ("resume-report.json" if args.resume else "report.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "error_type": error}, ensure_ascii=False))
    print("Report:", output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Simulation setup failed:", type(exc).__name__)
        raise SystemExit(1)

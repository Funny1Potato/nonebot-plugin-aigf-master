"""Start the runtime project's real FastAPI/OneBot server and connect over WS.

The controller acts as an external OneBot platform in a separate process from
NoneBot. All plugin data and transport credentials are isolated to this run.
"""
import argparse
import asyncio
import hashlib
import itertools
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


def serve(spec):
    workspace, run = Path(spec["workspace"]), Path(spec["run"])
    settings = spec["settings"]
    os.chdir(workspace)
    sys.path.insert(0, str(workspace))
    sys.path.insert(0, str(workspace / "aigf_manager/plugins"))
    for key in list(os.environ):
        if key.upper().startswith(("AIGFM_", "LOCALSTORE_", "ONEBOT_", "ROLLPIG_")):
            del os.environ[key]
    empty_config = run / "rollpig-empty.json"
    empty_config.write_text("{}", encoding="utf-8")
    os.environ["ROLLPIG_CONFIG_FILE"] = str(empty_config)
    import nonebot
    nonebot.init(_env_file=(str(run / "absent.env"),), driver="~fastapi+~httpx+~websockets",
        host="127.0.0.1", port=spec["port"], log_level="INFO", command_start={""},
        superusers={"980999"}, localstore_data_dir=run / "data",
        localstore_cache_dir=run / "cache", localstore_config_dir=run / "config",
        onebot_v11_access_token=spec["token"], onebot_v11_ws_urls=[], onebot_v11_api_roots={},
        aigfm_action_protocol="tools", aigfm_enabled_groups=[980101, 980102],
        aigfm_batch_timeout=0.2, aigfm_incomplete_timeout=0.2, aigfm_batch_count=2,
        aigfm_merge_window=0.05, aigfm_vlm_enabled=False, aigfm_llm_multimodal=False,
        aigfm_image_mode="vlm", aigfm_anime_backend="off", aigfm_search_enabled=spec.get("services", False),
        aigfm_invoke_enabled=spec.get("services", False), aigfm_learn_commands=False, aigfm_peer_bots=spec.get("peers", []),
        aigfm_capture_plugins=["echo"] if spec.get("services") else [],
        aigfm_image_gen_enabled=spec.get("services", False),
        rollpig_resource_sync_enabled=False, rollpig_roast_library_manifest_url="",
        rollpig_cloud_api_url=None, rollpig_ai_enabled=False, **settings)
    from nonebot.adapters.onebot.v11 import Adapter
    from nonebot.log import logger
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    diagnostics = []
    hidden = [spec["token"], *[str(v) for k, v in settings.items()
        if any(word in k for word in ("key", "url", "proxy")) and isinstance(v, str) and len(v) > 5]]

    def diagnostic(message):
        record = message.record
        text = record["message"]
        for value in hidden:
            text = text.replace(value, "<redacted>")
        diagnostics.append({"level": record["level"].name, "message": text[:400]})

    logger.add(diagnostic, level="WARNING")
    nonebot.load_from_toml(str(workspace / "pyproject.toml"))
    nonebot.load_builtin_plugins("echo")
    (run / "boot-diagnostics.json").write_text(json.dumps({
        "plugins": sorted(p.name for p in nonebot.get_loaded_plugins()), "diagnostics": diagnostics},
        ensure_ascii=False, indent=2), encoding="utf-8")
    plugin = nonebot.get_plugin("nonebot_plugin_aigf_master")
    if plugin is None:
        raise RuntimeError("runtime_plugin_load_failed")
    mod = plugin.module
    observed_batches = []
    if spec.get("services"):
        original_run = mod.MessageProcessor.run_batch

        async def observed_run(processor, *args, **kwargs):
            result = await original_run(processor, *args, **kwargs)
            observed_batches.append({"session": processor.session_key, "stop_reason": result.stop_reason,
                "usage": result.usage, "effects": [{"tool": e.get("tool", "send_legacy"), "status": e["status"],
                    "code": e["code"], "http_status": e.get("data", {}).get("http_status")}
                    for e in result.effects]})
            return result
        mod.MessageProcessor.run_batch = observed_run
    expected = workspace / "aigf_manager/plugins/nonebot_plugin_aigf_master/__init__.py"
    if Path(mod.__file__).resolve() != expected.resolve():
        raise RuntimeError("wrong_runtime_source")
    # Keep the unrelated PigHub resource refresh asleep during this test. Its
    # real startup and shutdown hooks still execute; no package files are edited.
    pig = nonebot.get_plugin("nonebot_plugin_rollpig_plus")
    if pig:
        import nonebot_plugin_rollpig_plus.pighub_service as pig_service
        pig_service.PIGHUB_STARTUP_REFRESH_DELAY_SECONDS = (3600, 3600)
    if not spec["live"]:
        async def scripted(**kwargs):
            if "tools" not in kwargs:
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps({"reply": ["WS_LEGACY"]}), tool_calls=[]))])
            prompt = kwargs["messages"][0]["content"]
            current = prompt.split("## 本批新消息与证据引用", 1)[-1].split("## 可用的本机命令", 1)[0]
            if kwargs["messages"][-1]["role"] == "tool":
                last = json.loads(kwargs["messages"][-1]["content"])
                if last["status"] == "committed":
                    marker = "WS_A_CHANGED" if "WS_A_CHANGED" in current else "WS_A_SAVED"
                    payload = {"messages": [{"key": marker, "parts": [
                        {"type": "at", "target": "980001"}, {"type": "text", "content": marker}]}]}
                    name = "send_message"
                else:
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[]))])
            elif "请把长期记忆" in current or "请在长期记忆" in current:
                path = mod._data_dir / "memory/onebot11_980101/long_term.json"
                doc = json.loads(path.read_text(encoding="utf-8"))
                meta = doc["_aigfm_meta"]["collections"]["facts"]
                op = {"action": "add", "value": "连接测试项目的主题色是绿色。"}
                if "WS_A_CHANGED" in current:
                    op = {"action": "modify", "entry_id": meta["ids"][0], "value": "连接测试项目的主题色是紫色。"}
                name, payload = "update_memory", {"target": "long_term", "expected_revision": meta["revision"], "operations": [op]}
            else:
                marker = "WS_B_HELLO" if "WS_B_HELLO" in current else "WS_AFTER_RESET" if "WS_AFTER_RESET" in current else "紫色"
                name, payload = "send_message", {"messages": [{"key": marker, "parts": [{"type": "text", "content": marker}]}]}
            call = SimpleNamespace(id=secrets.token_hex(8), function=SimpleNamespace(name=name, arguments=json.dumps(payload)))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))])
        mod._llm._create = scripted
    app = nonebot.get_app()
    from fastapi import Request, HTTPException
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=spec["port"], log_level="warning"))

    def authorize(request):
        if request.headers.get("Authorization") != "Bearer " + spec["token"]:
            raise HTTPException(status_code=401)

    @app.get("/__integration__/health")
    async def health(request: Request):
        authorize(request)
        batches = {}
        for key, proc in mod._processors.items():
            result = proc.last_process_result
            batches[key] = {"result_id": id(result) if result else None,
                "protocol": result.protocol if result else None,
                "next_protocol": proc.next_protocol,
                "stop_reason": result.stop_reason if result else None,
                "recent_count": len(proc.recent_messages),
                "effects": [{"tool": e.get("tool", "send_legacy"), "status": e["status"], "code": e["code"]} for e in result.effects] if result else []}
        return {"plugins": sorted(p.name for p in nonebot.get_loaded_plugins()),
                "bots": len(nonebot.get_bots()), "batches": batches,
                "startup_tasks": len(mod._tasks), "runtime_source": True,
                "peer_commands": mod._peer_scanned_commands,
                "peer_requests": [{k: v for k, v in row.items() if k in ("id", "peer", "session", "state", "outputs")}
                                  for row in mod._peer_client.requests.values()] if mod._peer_client else [],
                "peer_result_in_context": any("PEER_RESULT_29" in message.content and message.user_name == "integration_peer"
                    for proc in mod._processors.values() for message in proc.recent_messages)
                    or any("PEER_RESULT_29" in message.content and message.user_name == "integration_peer"
                        for chunk in mod._group_chunks.values() for message in chunk),
                "observed_batches": observed_batches[-100:],
                "pending_images": sum(len(p._image_gen_tasks) for p in mod._processors.values()),
                "chunks": {k: len(v) for k, v in mod._group_chunks.items()}}

    @app.post("/__integration__/shutdown")
    async def shutdown(request: Request):
        authorize(request)
        server.should_exit = True
        return {"ok": True}

    asyncio.run(server.serve())
    (run / ("server-" + str(spec["phase"]) + ".json")).write_text(json.dumps({
        "started": server.started, "diagnostics": diagnostics, "shutdown_returned": True},
        ensure_ascii=False, indent=2), encoding="utf-8")


async def exercise(args, repo, run, settings):
    import httpx
    import websockets
    checks, outputs, actions, health_records = {}, [], [], []
    sequence = itertools.count(1000000)
    token = secrets.token_urlsafe(32)
    source = args.workspace / "aigf_manager/plugins/nonebot_plugin_aigf_master"
    memory = run / "data/nonebot_plugin_aigf_master/memory"
    legacy = memory / "980101"
    legacy.mkdir(parents=True)
    original = b'{"items":["synthetic legacy record"]}'
    (legacy / "short_term.json").write_bytes(original)
    shutil.copytree(run / "data", run / "baseline-backup")

    async def phase(number):
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = candidate.getsockname()[1]
        spec = {"workspace": str(args.workspace), "run": str(run), "settings": settings,
                "port": port, "token": token, "live": args.live, "phase": number}
        child = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), "--server",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            cwd=args.workspace, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        child.stdin.write(json.dumps(spec).encode())
        await child.stdin.drain()
        child.stdin.close()
        root = f"http://127.0.0.1:{port}"
        client = httpx.AsyncClient(base_url=root, headers={"Authorization": "Bearer " + token}, trust_env=False, timeout=5)
        reader = None
        websocket = None
        try:
            async def health():
                response = await client.get("/__integration__/health")
                response.raise_for_status()
                return response.json()

            ready = None
            for _ in range(200):
                if child.returncode is not None:
                    raise RuntimeError("server_exited_before_ready")
                try:
                    ready = await health()
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            if not ready:
                raise TimeoutError("server_startup_timeout")
            health_records.append(ready)
            checks[f"phase_{number}_startup_hooks"] = ready["startup_tasks"] >= 2 and ready["runtime_source"]
            checks[f"phase_{number}_project_plugins"] = {"nonebot_plugin_aigf_master", "nonebot_plugin_rollpig_plus", "echo"} <= set(ready["plugins"])
            endpoint = f"ws://127.0.0.1:{port}/onebot/v11/ws"
            if number == 1:
                try:
                    async with websockets.connect(endpoint, additional_headers={"X-Self-ID": "980000"}, proxy=None):
                        checks["unauthorized_connection_rejected"] = False
                except websockets.exceptions.InvalidStatus as error:
                    checks["unauthorized_connection_rejected"] = error.response.status_code == 403
            websocket = await websockets.connect(endpoint, additional_headers={
                "X-Self-ID": "980000", "X-Client-Role": "Universal", "Authorization": "Bearer " + token}, proxy=None)

            async def platform():
                async for frame in websocket:
                    request = json.loads(frame)
                    action, data = request["action"], request.get("params", {})
                    actions.append(action)
                    if action.startswith("send_"):
                        if data.get("group_id") not in (980101, 980102):
                            raise RuntimeError("send_outside_test_groups")
                        outputs.append({"group": data["group_id"], "message": data.get("message", []), "phase": number})
                        result = {"message_id": next(sequence)}
                    elif action == "get_login_info":
                        result = {"user_id": 980000, "nickname": "外部模拟平台"}
                    elif action == "get_group_info":
                        result = {"group_id": data["group_id"], "group_name": "连接测试群"}
                    elif action in ("get_group_member_info", "get_stranger_info"):
                        result = {"user_id": data.get("user_id", 980001), "nickname": "模拟用户", "card": "", "role": "member", "sex": "unknown", "join_time": 1}
                    elif action == "get_group_member_list":
                        result = [{"user_id": uid, "nickname": "模拟用户", "card": "", "role": "member"} for uid in (980001, 980002, 980999)]
                    else:
                        result = {}
                    await websocket.send(json.dumps({"status": "ok", "retcode": 0, "data": result, "echo": request.get("echo")}))

            reader = asyncio.create_task(platform())

            async def send(text, group=980101, user=980001, mention=True):
                message = ([{"type": "at", "data": {"qq": "980000"}}] if mention else []) + [{"type": "text", "data": {"text": text}}]
                await websocket.send(json.dumps({"time": int(time.time()), "self_id": 980000, "post_type": "message",
                    "message_type": "group", "sub_type": "normal", "group_id": group, "user_id": user,
                    "message_id": next(sequence), "message": message, "raw_message": text, "font": 0,
                    "sender": {"user_id": user, "nickname": "模拟用户", "role": "admin"}}))

            def text(out):
                message = out["message"]
                return message if isinstance(message, str) else "".join(s["data"].get("text", "") for s in message if s["type"] == "text")

            async def wait_batch(group, previous=None):
                async def wait():
                    while True:
                        if reader.done():
                            await reader
                            raise RuntimeError("platform_reader_closed")
                        state = await health()
                        batch = state["batches"].get("onebot11:" + str(group), {})
                        if batch.get("result_id") and batch["result_id"] != previous:
                            health_records.append(state)
                            return batch
                        await asyncio.sleep(0.1)
                return await asyncio.wait_for(wait(), timeout=190)

            if number == 1:
                checks["startup_migrated_legacy_directory"] = not legacy.exists() and (memory / "onebot11_980101").exists()
                await send("禁用会话不应响应", group=980199)
                await send("请在长期记忆中记录连接测试项目主题色，下一条给出具体内容。")
                await send("连接测试项目主题色是绿色。保存成功后先 @我，再回复 WS_A_SAVED。")
                await send("请只回复 WS_B_HELLO，不修改记忆。", group=980102, user=980002)
                a, b = await asyncio.gather(wait_batch(980101), wait_batch(980102))
                checks["connected_over_real_websocket"] = (await health())["bots"] == 1 and bool(actions)
                checks["memory_then_native_reply"] = any(e["tool"] == "update_memory" and e["status"] == "committed" for e in a["effects"]) and any("WS_A_SAVED" in text(o) for o in outputs if o["group"] == 980101)
                checks["native_at_preserved"] = any(s["type"] == "at" and str(s["data"].get("qq")) == "980001" for o in outputs if o["group"] == 980101 and isinstance(o["message"], list) for s in o["message"])
                checks["sessions_isolated"] = any("WS_B_HELLO" in text(o) for o in outputs if o["group"] == 980102) and not any("WS_B_HELLO" in text(o) for o in outputs if o["group"] == 980101)
                checks["disabled_session_not_processed"] = not (memory / "onebot11_980199").exists()
                checks["tool_migration_backup"] = (memory / "onebot11_980101/short_term.json.pre-tools.bak").read_bytes() == original
                path = memory / "onebot11_980101/long_term.json"
                doc = json.loads(path.read_text(encoding="utf-8"))
                ids = doc["_aigfm_meta"]["collections"]["facts"]["ids"]
                await send("请把长期记忆中连接测试项目主题色从绿色改为紫色，修改原条目，成功后回复 WS_A_CHANGED。")
                changed = await wait_batch(980101, a["result_id"])
                doc = json.loads(path.read_text(encoding="utf-8"))
                checks["memory_correction_in_place"] = len(doc["facts"]) == 1 and "紫色" in doc["facts"][0] and ids == doc["_aigfm_meta"]["collections"]["facts"]["ids"]
                await send("reset", user=980999, mention=False)
                for _ in range(100):
                    state = await health()
                    if state["batches"]["onebot11:980101"]["recent_count"] == 0:
                        break
                    await asyncio.sleep(0.1)
                checks["reset_cleared_history"] = not (memory / "onebot11_980101/recent.json").exists() and state["batches"]["onebot11:980101"]["recent_count"] == 0
                checks["reset_kept_memory"] = "紫色" in path.read_text(encoding="utf-8")
                await send("请只回复 WS_AFTER_RESET，不改记忆。")
                await wait_batch(980101, changed["result_id"])
                checks["chat_after_reset"] = any("WS_AFTER_RESET" in text(o) for o in outputs if o["group"] == 980101)
                if args.offline:
                    async def wait_protocol(protocol):
                        async def poll():
                            while True:
                                state = await health()
                                if state["batches"]["onebot11:980101"]["next_protocol"] == protocol:
                                    return state
                                await asyncio.sleep(0.1)
                        return await asyncio.wait_for(poll(), 10)
                    await send("action_protocol legacy_json", user=980999, mention=False)
                    state = await wait_protocol("legacy_json")
                    checks["session_protocol_does_not_change_other_group"] = state["batches"]["onebot11:980102"]["next_protocol"] == "tools"
                    await send("请回复 WS_LEGACY")
                    legacy_batch = await wait_batch(980101, state["batches"]["onebot11:980101"]["result_id"])
                    checks["admin_protocol_command_changes_next_batch"] = legacy_batch["protocol"] == "legacy_json" and any("WS_LEGACY" in text(o) for o in outputs)
                    await send("status", user=980999, mention=False)
                    for _ in range(100):
                        if any("运行诊断" in text(o) for o in outputs):
                            break
                        await asyncio.sleep(0.1)
                    checks["status_exposes_diagnostics"] = any("运行诊断" in text(o) for o in outputs)
            else:
                if args.offline:
                    state = await health()
                    checks["restart_keeps_session_protocol"] = state["batches"]["onebot11:980101"]["next_protocol"] == "legacy_json"
                    await send("action_protocol default", user=980999, mention=False)
                    for _ in range(100):
                        if (await health())["batches"]["onebot11:980101"]["next_protocol"] == "tools":
                            break
                        await asyncio.sleep(0.1)
                    checks["session_protocol_can_revert_to_config"] = (await health())["batches"]["onebot11:980101"]["next_protocol"] == "tools"
                recent = memory / "onebot11_980101/recent.json"
                before = json.loads(recent.read_text(encoding="utf-8"))["messages"]
                await send("根据长期记忆，连接测试项目现在的主题色是什么？只回复颜色，不改记忆。")
                await wait_batch(980101)
                after = json.loads(recent.read_text(encoding="utf-8"))["messages"]
                checks["restart_restored_history"] = bool(before) and after[:len(before)] == before
                checks["restart_recalled_fact"] = any("紫色" in text(o) for o in outputs if o["phase"] == 2)
        finally:
            if websocket:
                await websocket.close()
            if reader:
                await asyncio.gather(reader, return_exceptions=True)
            try:
                await client.post("/__integration__/shutdown")
                await asyncio.wait_for(child.wait(), timeout=20)
            except Exception:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            checks[f"phase_{number}_graceful_shutdown"] = child.returncode == 0
            await client.aclose()

    error = None
    try:
        await phase(1)
        await phase(2)
    except Exception as exc:
        error = type(exc).__name__
    report = {"passed": bool(checks) and all(checks.values()) and error is None,
        "error_type": error, "checks": checks, "live_model": args.live,
        "real_network_transport": True, "real_chat_service": False, "synthetic_data_only": True,
        "api_actions": actions, "platform_sends": len(outputs), "health_records": health_records,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.py")}}
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("passed", "error_type", "checks")}, ensure_ascii=False))
    print("Report:", run / "report.json")
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--env-file", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.server:
        spec = json.loads(sys.stdin.read())
        try:
            serve(spec)
        except Exception as error:
            import traceback
            message = str(error)
            for value in [spec["token"], *spec["settings"].values()]:
                if isinstance(value, str) and len(value) > 5:
                    message = message.replace(value, "<redacted>")
            (Path(spec["run"]) / "server-setup-error.json").write_text(json.dumps({
                "type": type(error).__name__, "message": message[:1000],
                "frames": [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                           for f in traceback.extract_tb(error.__traceback__)]}, ensure_ascii=False), encoding="utf-8")
            raise
        return 0
    if not args.workspace or not (args.live or args.offline):
        parser.error("Explicit --workspace and --live/--offline are required")
    args.workspace = args.workspace.resolve()
    if args.live:
        if not args.env_file:
            parser.error("--live requires --env-file")
        from dotenv import dotenv_values
        path = args.env_file if args.env_file.is_absolute() else args.workspace / args.env_file
        if not path.is_file():
            parser.error("Environment file does not exist")
        values = {k.lower(): v for k, v in {**dotenv_values(args.workspace / ".env"), **dotenv_values(path),
            **{k: v for k, v in os.environ.items() if k.upper().startswith("AIGFM_")}}.items()}
        allowed = {"aigfm_llm_api_key", "aigfm_llm_base_url", "aigfm_llm_model", "aigfm_llm_temperature",
                   "aigfm_proxy_enabled", "aigfm_http_proxy", "aigfm_https_proxy"}
        settings = {k: v for k, v in values.items() if k in allowed and v is not None}
        if not all(settings.get(k) for k in ("aigfm_llm_api_key", "aigfm_llm_base_url", "aigfm_llm_model")):
            parser.error("Explicit model, API key and base URL are required")
    else:
        settings = {"aigfm_llm_api_key": "test-key", "aigfm_llm_base_url": "http://127.0.0.1:9/v1", "aigfm_llm_model": "scripted"}
    repo = Path(__file__).resolve().parents[1]
    results = repo / "tests/.results"
    results.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="connected-platform-", dir=results))
    return asyncio.run(exercise(args, repo, run, settings))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Connected-platform check failed:", type(exc).__name__)
        raise SystemExit(1)

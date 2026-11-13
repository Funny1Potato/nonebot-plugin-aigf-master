"""Live local-plugin, two-NoneBot peer HTTP, search and image service checks.

Only chat platforms are simulated. Existing service settings arrive over stdin;
real bot ports, peer tokens and data are never reused.
"""
import argparse
import asyncio
import base64
import hashlib
import itertools
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time


def peer_server(spec):
    root, run = Path(spec["workspace"]), Path(spec["run"])
    os.chdir(root)
    sys.path.insert(0, str(root))
    for key in list(os.environ):
        if key.upper().startswith(("AIGFM_", "LOCALSTORE_", "ONEBOT_")):
            del os.environ[key]
    import nonebot
    nonebot.init(_env_file=(str(run / "absent.env"),), driver="~fastapi+~httpx+~websockets",
        host="127.0.0.1", port=spec["port"], log_level="CRITICAL", command_start={""},
        localstore_data_dir=run / "peer-data", localstore_cache_dir=run / "peer-cache",
        localstore_config_dir=run / "peer-config", onebot_v11_access_token=spec["token"],
        onebot_v11_ws_urls=[], onebot_v11_api_roots={}, aigfm_peer_push_port=spec["master_port"],
        aigfm_peer_token=spec["token"], aigfm_peer_bot_name="integration_peer",
        aigfm_peer_capture_plugins=["echo"])
    from nonebot.adapters.onebot.v11 import Adapter
    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_builtin_plugins("echo")
    plugin = nonebot.load_plugin("nonebot_plugin_aigfm_peer")
    if plugin is None or Path(plugin.module.__file__).resolve() != (root / "nonebot_plugin_aigfm_peer/__init__.py").resolve():
        raise RuntimeError("wrong_peer_source")
    from fastapi import Request, HTTPException
    import uvicorn
    app = nonebot.get_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=spec["port"], log_level="warning"))

    def check(request):
        if request.headers.get("Authorization") != "Bearer " + spec["token"]:
            raise HTTPException(401)

    @app.get("/__integration__/health")
    async def health(request: Request):
        check(request)
        return {"bots": len(nonebot.get_bots()), "commands": plugin.module._commands_cache}

    @app.post("/__integration__/shutdown")
    async def shutdown(request: Request):
        check(request)
        server.should_exit = True
        return {"ok": True}
    asyncio.run(server.serve())


def free_port():
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


async def exercise(args, repo, run, settings):
    import httpx
    import websockets
    token = secrets.token_urlsafe(32)
    master_port, peer_port = free_port(), free_port()
    checks, blocked, processes, clients, connections, readers = {}, {}, {}, {}, {}, []
    output, actions, states = [], [], []
    ids = itertools.count(2000000)

    async def launch(role, port):
        spec = {"workspace": str(args.workspace), "run": str(run), "settings": settings if role == "master" else {},
                "port": port, "token": token, "live": True, "phase": "services", "services": True,
                "master_port": master_port, "peers": [{"name": "integration_peer", "port": peer_port, "token": token}]}
        child = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), "--server", role,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            cwd=args.workspace, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        processes[role] = child
        child.stdin.write(json.dumps(spec).encode())
        await child.stdin.drain()
        child.stdin.close()
        client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + token}, trust_env=False, timeout=5)
        clients[role] = client
        for _ in range(250):
            if child.returncode is not None:
                raise RuntimeError(role + "_startup_failed")
            try:
                response = await client.get("/__integration__/health")
                if response.status_code == 200:
                    checks[role + "_startup"] = True
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError(role + "_startup_timeout")
        websocket = await websockets.connect(f"ws://127.0.0.1:{port}/onebot/v11/ws",
            additional_headers={"Authorization": "Bearer " + token, "X-Self-ID": "980000", "X-Client-Role": "Universal"}, proxy=None)
        connections[role] = websocket

        async def platform():
            async for frame in websocket:
                request = json.loads(frame)
                action, data = request["action"], request.get("params", {})
                actions.append({"role": role, "action": action})
                if action.startswith("send_"):
                    if data.get("group_id") not in (980101, 980102):
                        raise RuntimeError("send_outside_test_groups")
                    message = data.get("message", [])
                    text = message if isinstance(message, str) else "".join(s["data"].get("text", "") for s in message if s["type"] == "text")
                    images = []
                    if isinstance(message, list):
                        for segment in message:
                            if segment["type"] == "image":
                                value = segment["data"].get("file", "")
                                if value.startswith("base64://"):
                                    binary = base64.b64decode(value.removeprefix("base64://"), validate=True)
                                    path = run / ("generated-" + str(next(ids)) + ".png")
                                    path.write_bytes(binary)
                                    from PIL import Image
                                    with Image.open(path) as picture:
                                        picture.verify()
                                    with Image.open(path) as picture:
                                        images.append({"file": path.name, "size": list(picture.size), "sha256": hashlib.sha256(binary).hexdigest()})
                    output.append({"role": role, "group": data["group_id"], "text": text, "images": images})
                    response = {"message_id": next(ids)}
                elif action == "get_login_info":
                    response = {"user_id": 980000, "nickname": "实服务测试"}
                elif action == "get_group_info":
                    response = {"group_id": data["group_id"], "group_name": "隔离测试群"}
                elif action in ("get_group_member_info", "get_stranger_info"):
                    response = {"user_id": data.get("user_id", 980001), "nickname": "测试用户", "card": "", "role": "member", "sex": "unknown", "join_time": 1}
                elif action == "get_group_member_list":
                    response = [{"user_id": uid, "nickname": "测试用户", "card": "", "role": "member"} for uid in (980001, 980002)]
                else:
                    response = {}
                await websocket.send(json.dumps({"status": "ok", "retcode": 0, "data": response, "echo": request.get("echo")}))
        readers.append(asyncio.create_task(platform()))

    async def send(role, text, group=980101):
        await connections[role].send(json.dumps({"time": int(time.time()), "self_id": 980000,
            "post_type": "message", "message_type": "group", "sub_type": "normal", "group_id": group,
            "user_id": 980001, "message_id": next(ids), "font": 0, "raw_message": text,
            "message": [{"type": "at", "data": {"qq": "980000"}}, {"type": "text", "data": {"text": text}}],
            "sender": {"user_id": 980001, "nickname": "测试用户", "role": "member"}}))

    async def health():
        r = await clients["master"].get("/__integration__/health")
        r.raise_for_status()
        state = r.json()
        peer_state = None
        if "peer" in clients:
            try:
                peer_state = (await clients["peer"].get("/__integration__/health")).json()
            except httpx.HTTPError:
                pass
        (run / "progress.json").write_text(json.dumps({"master": state, "peer": peer_state,
            "actions": actions, "output_markers": [{"role": o["role"], "group": o["group"],
                "peer_ready": "PEER_READY_23" in o["text"], "local_result": "LOCAL_RESULT_17" in o["text"],
                "peer_result": "PEER_RESULT_29" in o["text"], "images": o["images"]} for o in output]},
            ensure_ascii=False), encoding="utf-8")
        return state

    async def wait(predicate, timeout=190):
        async def poll():
            while True:
                for reader in readers:
                    if reader.done():
                        await reader
                        raise RuntimeError("platform_connection_closed")
                h = await health()
                if predicate(h):
                    states.append(h)
                    return h
                await asyncio.sleep(0.1)
        return await asyncio.wait_for(poll(), timeout)

    def effects(h, tool):
        return [e for batch in h.get("observed_batches", []) for e in batch["effects"] if e["tool"] == tool]

    failures = {}
    try:
        await launch("master", master_port)
        await launch("peer", peer_port)
        denied = await clients["peer"].post("/peer/invoke", headers={"Authorization": "Bearer invalid"}, json={"command": "echo", "group_id": 980101})
        checks["peer_unauthorized_rejected"] = denied.status_code == 401
        rejected = await clients["peer"].post("/peer/invoke", json={"command": "reset", "group_id": 980101})
        checks["peer_allowlist_rejected_management"] = rejected.status_code == 403
        await send("peer", "echo PEER_READY_23")
        await wait(lambda h: any(c.get("name") == "echo" for c in h.get("peer_commands", {}).get("integration_peer", [])), timeout=45)
        checks["peer_real_output_pushed_commands"] = any(o["role"] == "peer" and "PEER_READY_23" in o["text"] for o in output)

        async def case(name, operation):
            try:
                await operation()
            except Exception as error:
                failures[name] = type(error).__name__

        async def local_case():
            await send("master", "请调用本机插件命令 echo，文本参数 LOCAL_RESULT_17，只调用一次，等插件返回，不要自己模仿输出。")
            h = await wait(lambda h: any(e["status"] == "dispatched" for e in effects(h, "invoke_plugin")))
            await wait(lambda _: any(o["role"] == "master" and "LOCAL_RESULT_17" in o["text"] for o in output))
            checks["local_actual_plugin_invoked"] = True
            checks["local_quota_not_repeated"] = len(effects(h, "invoke_plugin")) == 1
        if args.checks in ("all", "routing"):
            await case("local", local_case)

        async def peer_case():
            await send("master", "请调用 integration_peer 的远程插件命令 echo，文本参数 PEER_RESULT_29，只调用一次；接收不等于执行成功，要等插件输出。")
            await wait(lambda h: any(e["status"] == "accepted" for e in effects(h, "invoke_peer_plugin")))
            await wait(lambda _: any(o["role"] == "peer" and "PEER_RESULT_29" in o["text"] for o in output))
            checks["peer_http_invocation_executed"] = True
            # Peer output returns via the real capture HTTP endpoint into master.
            h = await wait(lambda h: h.get("peer_result_in_context") is True)
            checks["peer_output_return_path"] = h["peer_result_in_context"]
        if args.checks in ("all", "routing"):
            await case("peer", peer_case)

        async def search_case():
            await send("master", "请调用 search_internet 搜索 NoneBot2 官方文档，只搜索一次。失败就如实说明，不能当作没有搜索结果。")
            h = await wait(lambda h: bool(effects(h, "search_internet")))
            result = effects(h, "search_internet")[-1]
            if result["status"] == "failed":
                blocked["search"] = {"code": result["code"], "http_status": result.get("http_status")}
                checks["search_failure_not_reported_as_empty_success"] = result["code"] in ("SEARCH_HTTP_ERROR", "SEARCH_SERVICE_ERROR")
            else:
                checks["search_live_results_received"] = True
        if args.checks in ("all", "search"):
            await case("search", search_case)

        async def image_case():
            await send("master", "请调用 generate_image 生成一张纯白背景上的蓝色圆形，无文字，无参考图；只提交一次任务，不要重复生图。")
            await wait(lambda h: any(e["status"] == "accepted" for e in effects(h, "generate_image")))
            checks["image_job_accepted"] = True
            await send("master", "请只回复 IMAGE_OTHER_SESSION，不要调用工具生图。", group=980102)
            await wait(lambda h: h["pending_images"] == 0, timeout=320)
            generated = [o for o in output if o["images"]]
            checks["image_service_returned_valid_image"] = bool(generated)
            checks["image_kept_original_session"] = bool(generated) and all(o["group"] == 980101 for o in generated)
            jobs = []
            for path in (run / "data").glob("**/tool_effects/*.json"):
                jobs.extend(e for e in json.loads(path.read_text(encoding="utf-8"))["effects"].values() if e.get("kind") == "job")
            checks["image_job_not_duplicated"] = len(jobs) == 1
            if not generated:
                blocked["image"] = {"code": "IMAGE_NOT_DELIVERED", "job_states": [j["status"] for j in jobs]}
        if args.checks in ("all", "image"):
            await case("image", image_case)
    except Exception as error:
        failures["setup"] = type(error).__name__
    finally:
        for websocket in connections.values():
            await websocket.close()
        await asyncio.gather(*readers, return_exceptions=True)
        for role, child in processes.items():
            try:
                await clients[role].post("/__integration__/shutdown")
                await asyncio.wait_for(child.wait(), 20)
            except Exception:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            checks[role + "_graceful_shutdown"] = child.returncode == 0
        for client in clients.values():
            await client.aclose()
    report = {"passed": bool(checks) and all(checks.values()) and not failures and not blocked,
        "checks": checks, "blocked_services": blocked, "failures": failures,
        "api_actions": actions, "states": states,
        "images": [image for o in output for image in o["images"]],
        "real_chat_service": False, "real_peer_http": True, "real_model_and_services": True}
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("passed", "checks", "blocked_services", "failures", "images")}, ensure_ascii=False))
    print("Report:", run / "report.json")
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", choices=["master", "peer"], help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--checks", choices=["all", "routing", "search", "image"], default="all")
    args = parser.parse_args()
    if args.server:
        spec = json.loads(sys.stdin.read())
        try:
            if args.server == "peer":
                peer_server(spec)
            else:
                from run_connected_platform import serve
                serve(spec)
        except Exception as error:
            (Path(spec["run"]) / (args.server + "-setup-error.json")).write_text(json.dumps({"type": type(error).__name__}), encoding="utf-8")
            raise
        return 0
    if not args.live or not args.workspace or not args.env_file:
        parser.error("Explicit --live, --workspace and --env-file are required")
    args.workspace = args.workspace.resolve()
    from dotenv import dotenv_values
    path = args.env_file if args.env_file.is_absolute() else args.workspace / args.env_file
    if not path.is_file():
        parser.error("Environment file does not exist")
    values = {k.lower(): v for k, v in {**dotenv_values(args.workspace / ".env"), **dotenv_values(path),
        **{k: v for k, v in os.environ.items() if k.upper().startswith("AIGFM_")}}.items()}
    allowed = {"aigfm_llm_api_key", "aigfm_llm_base_url", "aigfm_llm_model", "aigfm_llm_temperature",
        "aigfm_proxy_enabled", "aigfm_http_proxy", "aigfm_https_proxy", "aigfm_search_api", "aigfm_search_api_key",
        "aigfm_search_base_url", "aigfm_openwebsearch_url", "aigfm_image_gen_model", "aigfm_image_gen_api_key",
        "aigfm_image_gen_base_url", "aigfm_image_gen_min_size", "aigfm_image_gen_max_size", "aigfm_image_gen_watermark"}
    settings = {k: v for k, v in values.items() if k in allowed and v is not None}
    repo = Path(__file__).resolve().parents[1]
    results = repo / "tests/.results"
    results.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="live-services-", dir=results))
    return asyncio.run(exercise(args, repo, run, settings))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("Service check failed:", type(error).__name__)
        raise SystemExit(1)

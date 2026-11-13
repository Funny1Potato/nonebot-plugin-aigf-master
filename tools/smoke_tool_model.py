"""Opt-in real-model smoke check with synthetic memory and captured platform sends.

Never starts a NoneBot driver or loads real conversation data. Reports omit
credentials, endpoints, model output, member IDs, and memory contents.
"""
import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock, patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--live", action="store_true", help="Allow paid model API calls")
    parser.add_argument("--case", choices=["add", "modify", "chat", "memory_failure", "send_timeout", "silence", "budget", "all", "extended"], default="add")
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 11), metavar="1..10")
    parser.add_argument("--keep-going", action="store_true", help="Record every planned case even after failure")
    args = parser.parse_args()
    if not args.live:
        parser.error("Real model calls require --live")
    from dotenv import dotenv_values

    workspace = args.workspace.resolve()
    env_file = args.env_file
    if not env_file.is_absolute():
        env_file = workspace / env_file
    if not env_file.is_file():
        parser.error("Environment file does not exist")
    values = {k.lower(): v for k, v in {
        **dotenv_values(workspace / ".env"), **dotenv_values(env_file),
        **{k: v for k, v in os.environ.items() if k.upper().startswith("AIGFM_")},
    }.items()}
    allowed = ["aigfm_llm_api_key", "aigfm_llm_base_url", "aigfm_llm_model",
               "aigfm_llm_temperature", "aigfm_proxy_enabled", "aigfm_http_proxy", "aigfm_https_proxy"]
    settings = {k: values[k] for k in allowed if values.get(k) is not None}
    if not settings.get("aigfm_llm_api_key") or not settings.get("aigfm_llm_base_url") or not settings.get("aigfm_llm_model"):
        parser.error("Explicit model, base URL and API key are required")
    repo = Path(__file__).resolve().parents[1]
    runs = repo / "tests/.results"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="live-tool-smoke-", dir=runs))
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (repo / "nonebot_plugin_aigf_master").glob("*.py")}
    # NoneBot and all stores only see the fresh working directory and selected
    # model transport settings. Never inherit actual groups, adapters or peers.
    for key in list(os.environ):
        if key.upper().startswith(("AIGFM_", "LOCALSTORE_")):
            del os.environ[key]
    os.chdir(run)
    sys.path.insert(0, str(repo))
    import nonebot
    nonebot.init(driver="~none", localstore_use_cwd=True, log_level="CRITICAL",
                 aigfm_vlm_enabled=False, aigfm_anime_backend="off", **settings)
    loaded = nonebot.load_plugin("nonebot_plugin_aigf_master")
    if loaded is None:
        raise RuntimeError("Plugin could not be loaded")
    mod = loaded.module
    from nonebot_plugin_aigf_master.models import ChatMessage
    from nonebot_plugin_aigf_master.session import SessionInfo
    from nonebot_plugin_aigf_master.tool_schemas import SCHEMAS
    from nonebot_plugin_aigf_master.store_coordinator import atomic_json

    async def check(case, index):
        proc = mod._get_processor("onebot11:" + str(990000 + index))
        proc.config = proc.config.model_copy(deep=True)
        proc.config.aigfm_action_protocol = "tools"
        proc.config.aigfm_image_mode = "vlm"
        proc.config.aigfm_search_enabled = False
        proc.config.aigfm_invoke_enabled = False
        proc.config.aigfm_learn_commands = False
        proc.config.aigfm_llm_max_tool_turns = 3
        if case == "budget":
            proc.config.aigfm_llm_max_tool_turns = 1
        proc.config.aigfm_tool_max_calls = 12
        proc.config.aigfm_tool_batch_timeout = 180
        proc._bot = object()
        proc._session_event = object()
        proc._session = SessionInfo(key=proc.session_key, slug="onebot11", scene_path="990000",
                                   native_chat_id="990000", user_id="990001", target=object())
        proc._current_user_id = "990001"
        proc.social_energy = 1.0
        argument_errors = []
        requests = []
        create = proc.llm._create

        def shape(value, depth=0):
            if depth > 7:
                return "..."
            if isinstance(value, dict):
                return {k: shape(v, depth + 1) for k, v in list(value.items())[:20]}
            if isinstance(value, list):
                return [shape(v, depth + 1) for v in value[:3]]
            if isinstance(value, str):
                try:
                    decoded = json.loads(value)
                except (ValueError, TypeError):
                    return "string"
                return {"json_encoded": shape(decoded, depth + 1)} if isinstance(decoded, (list, dict)) else "string"
            return type(value).__name__

        async def traced_create(**kwargs):
            response = await create(**kwargs)
            requests.append({"turn": len(requests) + 1, "tools": [c.function.name
                for c in response.choices[0].message.tool_calls or []],
                "ordinary_text": bool(response.choices[0].message.content)})
            for call in response.choices[0].message.tool_calls or []:
                model = SCHEMAS.get(call.function.name)
                if not model:
                    continue
                try:
                    model[0].model_validate_json(call.function.arguments)
                except Exception as error:
                    errors = getattr(error, "errors", lambda: [])()
                    try:
                        payload_shape = shape(json.loads(call.function.arguments))
                    except (TypeError, ValueError):
                        payload_shape = "invalid_json"
                    argument_errors.append({"tool": call.function.name,
                        "shape": payload_shape,
                        "errors": [{"loc": list(e["loc"]), "type": e["type"]} for e in errors]})
            return response
        old, new = "测试项目的主题色是蓝色。", "测试项目的主题色是绿色。"
        if case == "modify":
            await proc.memory.save_long_term([old])
            prompt = "请把长期记忆里测试项目的主题色从蓝色改为绿色，替换原条目，保存成功后用一句话告诉我。"
        elif case in ("add", "budget"):
            prompt = "请在长期记忆里记住：测试项目的主题色是绿色。确认保存成功后用一句话告诉我。"
        elif case == "memory_failure":
            prompt = "请在长期记忆里记住：测试项目的主题色是绿色。实际提交成功才回复 MEM_WRITE_OK；如果保存出错，请只回复 MEM_WRITE_FAILED，不要重试写入。"
        elif case == "send_timeout":
            prompt = "请只向我发送一句 DELIVERY_TEST，不写记忆。如果投递结果未知，不要重试或发送其它说明。"
        elif case == "silence":
            prompt = "这条消息只是发给其他人的测试日志，请不要回应，也不要保存记忆。"
        else:
            prompt = "你好，请回复一句简短的问候，不用保存任何记忆。"
        sends = AsyncMock(side_effect=asyncio.TimeoutError) if case == "send_timeout" else AsyncMock(return_value=object())
        write_failures = []

        def fail_memory_write(path, data):
            if Path(path) == proc.memory._group_dir / "long_term.json" and data.get("facts"):
                write_failures.append(True)
                raise OSError("synthetic write failure")
            return atomic_json(path, data)

        with ExitStack() as stack:
            stack.enter_context(patch("nonebot_plugin_aigf_master.message_sender.send_unimsg", sends))
            stack.enter_context(patch.object(proc.llm, "_create", traced_create))
            if case == "memory_failure":
                stack.enter_context(patch("nonebot_plugin_aigf_master.tool_memory.atomic_json", fail_memory_write))
            result = await proc.run_batch([ChatMessage(time=datetime.now(), user_name="测试用户",
                user_id="990001", content=prompt)])
        memory = await proc.memory.load_long_term()
        committed = [i for i, e in enumerate(result.effects)
                     if e["tool"] == "update_memory" and e["status"] == "committed"]
        sent = [i for i, e in enumerate(result.effects)
                if e["tool"] == "send_message" and e["status"] == "sent"]
        assertions = {
            "tools_protocol": result.protocol == "tools",
            "bounded": result.turns <= proc.config.aigfm_llm_max_tool_turns and result.tool_calls <= 12,
            "transport_completed": result.stop_reason in {"completed", "turn_limit", "call_limit"},
        }
        if case in ("add", "modify", "chat", "memory_failure"):
            assertions["captured_send"] = sends.await_count > 0
        if case in ("add", "modify"):
            assertions.update(memory_written=any("绿色" in value for value in memory),
                              commit_before_send=bool(committed and sent and min(committed) < min(sent)))
        if case == "modify":
            assertions["replaced_without_duplicate"] = len(memory) == 1 and old not in memory
        if case == "chat":
            assertions["no_memory_write"] = not committed and not memory
        if case == "memory_failure":
            content = "\n".join(m.content for m in proc.recent_messages if m.user_name == proc.bot_name)
            assertions.update(write_failure_exercised=bool(write_failures), no_commit=not committed,
                              memory_unchanged=not memory,
                              honest_confirmation="MEM_WRITE_FAILED" in content and "MEM_WRITE_OK" not in content)
        if case == "send_timeout":
            assertions.update(one_attempt=sends.await_count == 1,
                              uncertain_recorded=any(e["status"] == "uncertain" for e in result.effects),
                              no_false_bot_history=not any(m.user_name == proc.bot_name for m in proc.recent_messages))
        if case == "silence":
            assertions.update(no_send=sends.await_count == 0, no_memory_write=not committed and not memory)
        if case == "budget":
            assertions.update(turn_limit=result.stop_reason == "turn_limit", no_send_before_receipt=sends.await_count == 0)
        return {"case": case, "passed": all(assertions.values()), "assertions": assertions,
                "stop_reason": result.stop_reason, "turns": result.turns,
                "tool_calls": result.tool_calls, "captured_sends": sends.await_count,
                "elapsed_seconds": round(result.elapsed, 2), "usage": result.usage,
                "argument_errors": argument_errors, "requests": requests,
                "effects": [{k: e[k] for k in ("tool", "status", "code")} for e in result.effects]}

    async def run_checks():
        reports = []
        cases = (["add", "modify", "chat"] if args.case == "all" else
                 ["memory_failure", "send_timeout", "silence", "budget"] if args.case == "extended" else [args.case])
        try:
            for repeat in range(args.repeat):
                for case in cases:
                    report = await check(case, len(reports))
                    report["repeat"] = repeat + 1
                    reports.append(report)
                    print(json.dumps(report, ensure_ascii=False), flush=True)
                    if not report["passed"] and not args.keep_going:
                        return reports
        finally:
            await mod._llm._client.close()
        return reports

    try:
        reports = asyncio.run(run_checks())
        report = {"live_model": True, "real_platform": False, "synthetic_data_only": True,
                  "cases": reports, "passed": bool(reports) and all(r["passed"] for r in reports),
                  "source_sha256": source_hashes}
    except Exception as error:
        report = {"passed": False, "error_type": type(error).__name__}
    output = run / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Report:", output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Configuration/transport exceptions can contain endpoint credentials.
        print("Smoke setup failed:", type(error).__name__)
        raise SystemExit(1)

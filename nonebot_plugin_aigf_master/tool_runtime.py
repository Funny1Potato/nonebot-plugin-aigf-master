"""Bounded tool-only conversation. Ordinary assistant text is never an action."""
import asyncio
from dataclasses import dataclass, field
import json
import time
from uuid import uuid4

from nonebot import logger
from pydantic import ValidationError

from .llm_client import _build_user_message
from .store_coordinator import fingerprint
from .tool_schemas import SCHEMAS


def receipt(status, code, data=None, *, ok=None, **extra):
    if ok is None:
        ok = status in {"ok", "sent", "committed", "unchanged", "accepted", "dispatched", "saved", "already_exists", "pending_delete", "deleted"}
    return {"ok": ok, "status": status, "code": code, "data": data or {}, "warnings": [], **extra}


@dataclass
class ProcessResult:
    protocol: str
    stop_reason: str
    turns: int = 0
    tool_calls: int = 0
    effects: list = field(default_factory=list)
    legacy_reply: list | None = None
    usage: dict = field(default_factory=dict)
    elapsed: float = 0.0
    ignored_texts: int = 0


@dataclass
class BatchContext:
    invocation: object
    generation: int
    owner: object
    timeout: float
    batch_id: str = field(default_factory=lambda: uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    sends: int = 0
    memory_changes: int = 0
    evidence: dict = field(default_factory=dict)
    effects: list = field(default_factory=list)
    generation_jobs: dict = field(default_factory=dict)
    energy_characters: int = 0
    energy_base_paid: bool = False
    send_halted: bool = False

    @property
    def remaining(self):
        return max(0.0, self.timeout - (time.monotonic() - self.started))

    @property
    def current(self):
        return getattr(self.owner, "_generation", 0) == self.generation


class ToolRuntime:
    def __init__(self, llm, config, context):
        self.llm, self.config, self.context = llm, config, context
        self.calls = 0
        self.cache = {}
        self.semantic = {}
        self.transcript = []

    async def run(self, prompt, tools, handler, images=None):
        self.transcript = [_build_user_message(prompt, images)]
        allowed = {t["function"]["name"] for t in tools}
        stop, turns = "turn_limit", 0
        usage, ignored = {}, 0
        try:
            for turn in range(self.config.aigfm_llm_max_tool_turns):
                if not self.context.current:
                    stop = "reset"
                    break
                if not self.context.remaining:
                    stop = "deadline"
                    break
                response = await asyncio.wait_for(self.llm._create(
                    model=self.config.aigfm_llm_model, messages=list(self.transcript), tools=tools,
                    temperature=self.config.aigfm_llm_temperature,
                    timeout=min(60, self.context.remaining)), timeout=min(60, self.context.remaining))
                turns += 1
                reported = getattr(response, "usage", None)
                if reported is not None:
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        value = getattr(reported, key, None)
                        if isinstance(value, int):
                            usage[key] = usage.get(key, 0) + value
                message = response.choices[0].message
                calls = message.tool_calls or []
                if not calls:
                    if message.content:
                        ignored += 1
                        logger.debug(f"[工具协议] batch={self.context.batch_id} 忽略普通结束文本 len={len(message.content)}")
                    stop = "completed"
                    break
                self.transcript.append({"role": "assistant", "content": message.content,
                                        "tool_calls": [{"id": c.id, "type": "function", "function": {
                                            "name": c.function.name, "arguments": c.function.arguments}} for c in calls]})
                failed = False
                for index, call in enumerate(calls):
                    name, raw = call.function.name, call.function.arguments
                    try:
                        digest = fingerprint([name, json.loads(raw)])
                    except (ValueError, TypeError):
                        digest = fingerprint([name, raw])
                    if index >= 4 or self.calls >= self.config.aigfm_tool_max_calls:
                        result = receipt("failed", "TOOL_BUDGET_EXCEEDED")
                    else:
                        self.calls += 1
                        if call.id in self.cache:
                            old_digest, result = self.cache[call.id]
                            if old_digest != digest:
                                result = receipt("failed", "TOOL_CALL_ID_CONFLICT")
                        else:
                            result = await self.execute(name, raw, allowed, handler, failed)
                            self.cache[call.id] = (digest, result)
                    result = {**result, "request_id": call.id}
                    self.context.effects.append({"tool": name, **result})
                    failed = failed or not result["ok"] or result["status"] == "uncertain"
                    self.transcript.append({"role": "tool", "tool_call_id": call.id,
                                            "content": json.dumps(result, ensure_ascii=False)})
                if not self.context.current:
                    stop = "reset"
                    break
                if self.calls >= self.config.aigfm_tool_max_calls:
                    stop = "call_limit"
                    break
        except asyncio.TimeoutError:
            stop = "deadline" if not self.context.remaining else "request_timeout"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(f"[工具协议] batch={self.context.batch_id} 请求失败: {type(error).__name__}")
            stop = "request_error"
        logger.info(f"[工具协议] batch={self.context.batch_id} stop={stop} turns={turns} calls={self.calls}")
        return ProcessResult("tools", stop, turns, self.calls, list(self.context.effects),
                             usage=usage, elapsed=time.monotonic() - self.context.started, ignored_texts=ignored)

    async def execute(self, name, raw, allowed, handler, failed):
        if name not in allowed or name not in SCHEMAS:
            return receipt("failed", "TOOL_NOT_AVAILABLE")
        if not self.context.current:
            return receipt("failed", "SESSION_RESET")
        if failed and name == "send_message":
            return receipt("failed", "DEPENDENCY_RESULT_REQUIRED")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("arguments must be an object")
            value = SCHEMAS[name][0].model_validate(payload)
        except ValidationError as error:
            errors = [{"path": list(e["loc"]), "type": e["type"], "message": (
                '需要 JSON 数组 [...]；不能使用 {"item":...} 对象，也不能把数组编码成字符串。'
                if e["type"] == "list_type" else e["msg"][:200])}
                      for e in error.errors(include_input=False, include_url=False)[:8]]
            return receipt("failed", "INVALID_ARGUMENTS", {
                "hint": "按字段错误和工具 schema 修正参数；不能用空参数替代", "errors": errors})
        except (ValueError, TypeError):
            return receipt("failed", "INVALID_ARGUMENTS", {"hint": "按工具 schema 修正参数；不能用空参数替代"})
        canonical = fingerprint([name, value.model_dump(mode="json")])
        if name not in {"query_memory", "query_commands", "search_internet"} and canonical in self.semantic:
            return self.semantic[canonical]
        if not self.context.remaining:
            return receipt("failed", "DEADLINE_EXCEEDED")
        try:
            result = await asyncio.wait_for(handler(name, value), timeout=self.context.remaining)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            result = receipt("uncertain", "TOOL_TIMEOUT", ok=False)
        except Exception as error:
            logger.error(f"[工具协议] tool={name} error={type(error).__name__}")
            result = receipt("uncertain", "TOOL_ERROR", {
                "hint": "结果未确认，不要重复写入或宣称成功；必要时只读查询一次确认实际状态，然后向用户说明。"}, ok=False)
        if name not in {"query_memory", "query_commands", "search_internet"}:
            self.semantic[canonical] = result
        return result

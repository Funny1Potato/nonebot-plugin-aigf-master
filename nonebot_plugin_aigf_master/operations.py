"""Session protocol overrides and bounded, content-free batch diagnostics."""
from collections import deque
from datetime import datetime

from .store_coordinator import atomic_json, read_json

PROTOCOLS = {"legacy_json", "tools"}


class ProtocolOverrides:
    def __init__(self, path):
        self.path = path

    def _read(self):
        document = read_json(self.path, {"version": 1, "sessions": {}})
        sessions = document.get("sessions")
        if (document.get("version") != 1 or not isinstance(sessions, dict)
                or any(not isinstance(k, str) or ":" not in k or not isinstance(v, str) or v not in PROTOCOLS
                       for k, v in sessions.items())):
            raise ValueError("invalid protocol override store; original file preserved")
        return document

    def resolve(self, key, config):
        return self._read()["sessions"].get(key, config.aigfm_session_protocols.get(
            key, config.aigfm_action_protocol))

    def set(self, key, protocol):
        if ":" not in key or protocol not in PROTOCOLS | {"default"}:
            raise ValueError("invalid session protocol")
        document = self._read()
        if protocol == "default":
            document["sessions"].pop(key, None)
        else:
            document["sessions"][key] = protocol
        atomic_json(self.path, document)


class BatchDiagnostics:
    def __init__(self):
        self.records = deque(maxlen=20)

    def record(self, result):
        self.records.append({
            "at": datetime.now().strftime("%H:%M:%S"), "protocol": result.protocol,
            "stop": result.stop_reason, "turns": result.turns, "calls": result.tool_calls,
            "elapsed": result.elapsed, "usage": dict(result.usage), "ignored": result.ignored_texts,
            "failures": [(e.get("tool", "send"), e.get("code", e.get("status", "failed")))
                         for e in result.effects if not e.get("ok", False) or e.get("status") == "uncertain"],
        })

    def render(self):
        if not self.records:
            return "运行诊断：尚无批次"
        lines = [f"运行诊断（最近 {len(self.records)} 批，重启清空）："]
        for row in list(self.records)[-5:]:
            tokens = row["usage"].get("total_tokens", "未报告")
            lines.append(f"{row['at']} {row['protocol']} / {row['stop']}，"
                         f"{row['elapsed']:.2f}s，轮次 {row['turns']}，工具 {row['calls']}，"
                         f"token {tokens}，忽略普通文本 {row['ignored']}")
        failures = [(r["at"], tool, code) for r in self.records for tool, code in r["failures"]]
        limits = sum(r["stop"] in {"turn_limit", "call_limit", "deadline"} for r in self.records)
        lines.append(f"窗口内预算/截止耗尽 {limits} 批；工具失败/不确定 {len(failures)} 次")
        lines.extend(f"{at} {tool}: {code}" for at, tool, code in failures[-5:])
        return "\n".join(lines)

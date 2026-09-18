"""LLM 响应解析（纯函数）"""

import json
import re

from .models import MemoryOps, ReplySegment

# 响应里会出现这些顶层键：兜底扫描时优先选含它们的对象，避免只抽到内层片段（如 memory 里的 short_term）
_RESPONSE_KEYS = ("reply", "memory", "command_learning", "command_edit", "command_delete")


def _escape_inner_quotes(text: str) -> str:
    """给字符串值里**未转义**的双引号补上反斜杠（LLM 常见：content 里写「喊她"我妈"」而没转义）

    判据：字符串内的 `"` 之后跳过空白，若紧跟 `,` `:` `}` `]` 或到达串尾，才算字符串结束；
    否则视为内容里的字面量引号。对合法 JSON 是恒等变换（一个字都不改）。
    """
    n = len(text)
    out: list[str] = []
    in_str = False
    esc = False
    for idx, ch in enumerate(text):
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch != '"':
            out.append(ch)
            continue
        if not in_str:
            in_str = True
            out.append(ch)
            continue
        j = idx + 1
        while j < n and text[j] in " \t\r\n":
            j += 1
        if j >= n or text[j] in ",:}]":
            in_str = False
            out.append(ch)
        else:
            out.append('\\"')
    return "".join(out)


def _extract_json_object(text: str) -> str | None:
    """扫描每个 '{' 起点做花括号平衡（正确处理字符串内的 {} 与 \\" 转义），
    返回可解析为 JSON 的对象子串；**优先含已知顶层键的那个**，都不含时才退回第一个；
    找不到返回 None。"""
    n = len(text)
    first_any: str | None = None
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j < n:
            candidate = text[i:j + 1]
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                if first_any is None:
                    first_any = candidate
                if any(key in data for key in _RESPONSE_KEYS):
                    return candidate
        i += 1
    return first_any


def parse_llm_response(raw: str) -> dict | None:
    """解析 LLM 返回的 JSON，去除代码块标记；容忍前导/尾随文字与未转义的字符串内引号"""
    if not raw:
        return None
    cleaned = re.sub(r"^```json\s*|\s*```$", "", raw.strip())
    # 先整体严格解析（原文优先、其次修复内嵌引号后的文本）；
    # 整体都失败才退回花括号平衡扫描——否则「第一个能解析的片段」可能是内层对象（如 memory 里的 short_term），
    # 那样会拿到没有 reply 的 dict，回复就发不出去了
    texts = (cleaned, _escape_inner_quotes(cleaned))
    for text in texts:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    for text in texts:
        candidate = _extract_json_object(text)
        if candidate:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return None


def extract_reply_segments(data: dict) -> list[ReplySegment]:
    """从 LLM 响应中提取回复段落"""
    reply_raw = data.get("reply", [])
    segments: list[ReplySegment] = []
    for item in reply_raw:
        if isinstance(item, str):
            if item:
                segments.append(ReplySegment(type="text", content=item))
        elif isinstance(item, dict):
            msg_type = item.get("type", "text")
            if msg_type == "meme":
                mid = item.get("id", "")
                if mid:
                    segments.append(ReplySegment(type="meme", meme_id=mid))
            elif msg_type == "at":
                name = item.get("name", "")
                if name:
                    segments.append(ReplySegment(type="at", user_name=name))
            elif msg_type == "text":
                content = item.get("content", "")
                if content:
                    segments.append(ReplySegment(type="text", content=content))
    return segments


def extract_memory_ops(data: dict) -> MemoryOps:
    """从 LLM 响应中提取记忆操作指令"""
    raw = data.get("memory", {})
    if not raw:
        return MemoryOps()
    return MemoryOps(
        short_term=raw.get("short_term"),
        long_term=raw.get("long_term"),
        friends=raw.get("friends"),
        save_meme=raw.get("save_meme"),
        culture=raw.get("culture"),
    )


def extract_command_learning(data: dict) -> dict:
    """从 LLM 响应中提取命令学习数据"""
    result = {"learn": [], "edit": [], "delete": []}

    learn_raw = data.get("command_learning", [])
    if isinstance(learn_raw, list):
        for item in learn_raw:
            if isinstance(item, dict) and item.get("name"):
                result["learn"].append({
                    "name": item["name"],
                    "parameters": item.get("parameters", ""),
                    "usage": item.get("usage", ""),
                    "hook_type": item.get("hook_type", "current"),
                    "source": item.get("source", ""),
                    "examples": item.get("examples", []),
                })

    edit_raw = data.get("command_edit", [])
    if isinstance(edit_raw, list):
        for item in edit_raw:
            if isinstance(item, dict) and item.get("name"):
                result["edit"].append({
                    "name": item["name"],
                    "parameters": item.get("parameters"),
                    "usage": item.get("usage"),
                    "examples": item.get("examples"),
                })

    delete_raw = data.get("command_delete", [])
    if isinstance(delete_raw, list):
        for item in delete_raw:
            if isinstance(item, str) and item:
                result["delete"].append(item)

    return result

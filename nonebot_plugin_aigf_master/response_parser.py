"""LLM 响应解析（纯函数）"""

import json
import re

from .models import MemoryOps, ReplySegment


def _extract_json_object(text: str) -> str | None:
    """扫描每个 '{' 起点做花括号平衡（正确处理字符串内的 {} 与 \\" 转义），
    返回第一个能成功解析为 JSON 的对象子串；找不到返回 None。"""
    n = len(text)
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
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        i += 1
    return None


def parse_llm_response(raw: str) -> dict | None:
    """解析 LLM 返回的 JSON，去除 think 标签和代码块标记；容忍前导/尾随文字"""
    cleaned = re.sub(r"^```json\s*|\s*```$", "", raw.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        candidate = _extract_json_object(cleaned)
        if candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None
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
        save_image=raw.get("save_image"),
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

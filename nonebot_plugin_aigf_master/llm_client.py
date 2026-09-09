"""LLM 客户端封装"""

import json
import re

import httpx
from nonebot import logger
from openai import AsyncOpenAI


def _strip_think_tags(text: str) -> str:
    """去除 LLM 输出中的 <think> 标签"""
    pattern = r"^(?:\s*<think>(.*?)</think>\s*|\s*<think\s*/?>\s*)+"
    return re.sub(pattern, "", text, flags=re.DOTALL).lstrip()


def make_http_client(proxy: str | None = None) -> httpx.AsyncClient | None:
    if not proxy:
        return None
    return httpx.AsyncClient(proxy=proxy)


class LLMClient:
    """LLM 客户端"""

    def __init__(self, api_key: str, base_url: str, proxy: str | None = None):
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            http_client=make_http_client(proxy),
        )

    async def chat(
        self, prompt: str, model: str,
        json_mode: bool = False, images: list[str] | None = None,
    ) -> str | None:
        if images:
            content = [{"type": "text", "text": prompt}]
            for img_b64 in images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": prompt}]

        kwargs: dict = {"messages": messages, "model": model, "temperature": 0.5, "timeout": 300}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return _strip_think_tags(content) if content else None

    async def chat_with_tools(
        self, prompt: str, model: str,
        tools: list[dict], tool_handler,
        json_mode: bool = False,
        first_call_json: bool = False,
    ) -> tuple[str | None, bool]:
        """支持 function calling 的响应生成

        first_call_json: 首次带 tools 的请求是否也强制 JSON 输出。
        默认 False（避免与 function calling 冲突，部分兼容服务不支持两者组合）；
        工具回填后的最终答复始终按 json_mode 强制 JSON。
        """
        messages = [{"role": "user", "content": prompt}]
        used_tool = False

        kwargs: dict = {"messages": messages, "model": model, "tools": tools, "temperature": 0.5, "timeout": 300}
        if json_mode and first_call_json:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message

        if message.tool_calls:
            messages.append(message)
            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    logger.error(f"[LLM] 工具参数解析失败: {tool_call.function.arguments}")
                    func_args = {}
                logger.info(f"[LLM] 调用工具: {func_name}, 参数: {func_args}")
                try:
                    result = await tool_handler(func_name, func_args)
                    used_tool = True
                except Exception as e:
                    logger.error(f"[LLM] 工具执行失败: {e}")
                    result = f"工具执行失败: {e}"
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

            kwargs: dict = {"messages": messages, "model": model, "temperature": 0.5, "timeout": 300}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            response = await self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            return (_strip_think_tags(content) if content else None), used_tool

        content = message.content
        return (_strip_think_tags(content) if content else None), False

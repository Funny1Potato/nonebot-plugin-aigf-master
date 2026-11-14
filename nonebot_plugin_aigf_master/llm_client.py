"""LLM 客户端封装"""

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from contextvars import ContextVar

import httpx
from nonebot import logger
from openai import AsyncOpenAI

# 工具回填后最终回复的墙钟超时（openai 的 timeout 是逐读块语义，慢响应可能无界拖住）
_FINAL_ROUND_TIMEOUT = 300.0
batch_usage = ContextVar("aigfm_batch_usage", default=None)

# 探测主模型是否接受图片输入用的 16x16 PNG（base64 仅 112 字符，一次请求成本可忽略）
_PROBE_IMAGE_B64 = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGklEQVR42mOs6FnAQApgYiARjGoY1TB0NAAANysBxN46f1oAAAAASUVORK5CYII="

# 明确表示「请求内容被服务端拒绝」的状态码：据此判定主模型不支持图片输入。
# 401/403/404（鉴权、模型名）、429（限流）、5xx 与网络异常都归为「探测不出结论」，不改变判定。
_CONTENT_REJECT_STATUS = frozenset({400, 415, 422})


@dataclass(frozen=True)
class ImagePayload:
    """随请求发送的一张图片。

    image_id 是缓存里的图片 id（prompt 里以 [发送了一张图片, id: xxx] 标注），
    带上它才能在多模态内容里把「图片本体」和「prompt 里的标注」一一对上。
    """

    image_id: str
    data_b64: str


def _strip_think_tags(text: str) -> str:
    """去除 LLM 输出中的 <think> 标签"""
    pattern = r"^(?:\s*<think>(.*?)</think>\s*|\s*<think\s*/?>\s*)+"
    return re.sub(pattern, "", text, flags=re.DOTALL).lstrip()


def make_http_client(proxy: str | None = None) -> httpx.AsyncClient | None:
    if not proxy:
        return None
    return httpx.AsyncClient(proxy=proxy)


def _sniff_image_mime(img_b64: str) -> str:
    """按 base64 数据的魔数判断图片 MIME。

    不看文件名：缓存里的表情包可能是 png/gif/jpeg，声明成 image/jpeg 会让严格校验
    MIME 的服务端直接拒收（PNG 字节配 jpeg 声明）。识别不出时保守按 jpeg 处理。
    """
    head = img_b64[:24].rstrip("=")
    if not head:
        return "image/jpeg"
    try:
        raw = base64.b64decode(head + "=" * (-len(head) % 4))
    except Exception:
        return "image/jpeg"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


def _has_image(messages) -> bool:
    """判断一次请求里是否带了图片"""
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        if any(isinstance(c, dict) and c.get("type") == "image_url" for c in content):
            return True
    return False


def _is_content_rejected(e: Exception) -> bool:
    """服务端是否明确拒绝了请求内容（而非鉴权失败、限流或服务端故障）"""
    status = getattr(e, "status_code", None)
    return isinstance(status, int) and status in _CONTENT_REJECT_STATUS


def _build_user_message(prompt: str, images: list[ImagePayload] | None = None) -> dict:
    """构造用户消息；有图片时使用 OpenAI 兼容的多模态内容格式。

    每张图片前面插一个 [图片 id: xxx] 文本块作锚点：prompt 正文里的
    [发送了一张图片, id: xxx] 与图片本体是分开传的，没有锚点时模型无法判断
    哪个 id 对应哪张图（多图时尤其明显）。
    """
    if not images:
        return {"role": "user", "content": prompt}
    content = [{"type": "text", "text": prompt}]
    for img in images:
        if img.image_id:
            content.append({"type": "text", "text": f"[图片 id: {img.image_id}]"})
        mime = _sniff_image_mime(img.data_b64)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{img.data_b64}"},
        })
    return {"role": "user", "content": content}


class LLMClient:
    """LLM 客户端"""

    def __init__(self, api_key: str, base_url: str, proxy: str | None = None):
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            http_client=make_http_client(proxy),
        )
        # 主模型是否支持图片输入：None=尚未判定（按支持处理），True=支持，False=不支持
        self._multimodal: bool | None = None

    @property
    def multimodal(self) -> bool:
        """主模型当前是否按「支持图片输入」处理。

        尚未判定时保守地按支持处理：真实错误照常抛出，不被回落逻辑掩盖。
        """
        return self._multimodal is not False

    def set_multimodal(self, value: bool) -> None:
        """按配置或探测结果直接设定主模型的图片能力"""
        self._multimodal = value

    async def _create(self, **kwargs):
        """统一请求入口：带图请求被服务端明确拒绝时，标记主模型不支持图片输入。

        标记只影响「下一批消息」（解析期才知道要不要走 VLM 描述），当前这批仍按失败处理。
        """
        try:
            response = await self._client.chat.completions.create(**kwargs)
            metrics = batch_usage.get()
            if metrics is not None:
                metrics["turns"] += 1
                reported = getattr(response, "usage", None)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = getattr(reported, key, None)
                    if isinstance(value, int):
                        metrics["usage"][key] = metrics["usage"].get(key, 0) + value
            return response
        except Exception as e:
            if _has_image(kwargs.get("messages")) and _is_content_rejected(e):
                self._multimodal = False
                logger.warning(
                    "[LLM] 主模型拒绝图片输入，已标记为不支持多模态（下一批消息起回落到 VLM 模式）")
            raise

    async def probe_multimodal(self, model: str) -> bool | None:
        """探测主模型是否接受图片输入。

        返回 True=支持；False=不支持（调用方应回落到 VLM 模式）；
        None=探测不出结论（鉴权、限流、网络等），调用方应保持原判定，不要掩盖真实错误。
        """
        try:
            await self._client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ok"},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{_PROBE_IMAGE_B64}"}},
                    ],
                }],
                temperature=0.5,
                timeout=30,
            )
        except Exception as e:
            if _is_content_rejected(e):
                logger.warning(
                    f"[LLM] 主模型不支持图片输入（探测请求被拒绝: "
                    f"{getattr(e, 'status_code', '?')}）")
                return False
            logger.warning(f"[LLM] 图片能力探测未得到结论，保持原判定: {e}")
            return None
        return True

    async def chat(
        self, prompt: str, model: str,
        json_mode: bool = False, images: list[ImagePayload] | None = None,
    ) -> str | None:
        messages = [_build_user_message(prompt, images)]

        kwargs: dict = {"messages": messages, "model": model, "temperature": 0.5, "timeout": 300}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._create(**kwargs)
        content = response.choices[0].message.content
        return _strip_think_tags(content) if content else None

    async def chat_with_tools(
        self, prompt: str, model: str,
        tools: list[dict], tool_handler,
        json_mode: bool = False,
        first_call_json: bool = False,
        images: list[ImagePayload] | None = None,
    ) -> tuple[str | None, bool]:
        """支持 function calling 的响应生成

        first_call_json: 首次带 tools 的请求是否也强制 JSON 输出。
        默认 False（避免与 function calling 冲突，部分兼容服务不支持两者组合）；
        工具回填后的最终答复始终按 json_mode 强制 JSON。
        images: 可选的图片，与普通回复路径一样随首轮请求发送。
        """
        messages = [_build_user_message(prompt, images)]
        used_tool = False

        kwargs: dict = {"messages": messages, "model": model, "tools": tools, "temperature": 0.5, "timeout": 300}
        if json_mode and first_call_json:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._create(**kwargs)
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

            logger.info(f"[LLM] 工具已回填（{len(message.tool_calls)} 个），请求最终回复")
            # 提示层兜底：降低模型「工具后还想再调 / 空答」的概率
            messages.append({"role": "user", "content": "工具结果已在上方。请直接给出最终回复（JSON），不要再请求工具。"})
            kwargs: dict = {"messages": messages, "model": model, "temperature": 0.5, "timeout": _FINAL_ROUND_TIMEOUT}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                response = await asyncio.wait_for(
                    self._create(**kwargs), timeout=_FINAL_ROUND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"[LLM] 最终回复超时（{_FINAL_ROUND_TIMEOUT:.0f}s）")
                return None, used_tool
            message = response.choices[0].message
            if message.tool_calls:
                logger.warning(f"[LLM] 最终轮仍返回工具请求: {[c.function.name for c in message.tool_calls]}（main 单轮不支持续跑，按空响应处理）")
                return None, used_tool
            content = _strip_think_tags(message.content) if message.content else None
            if not content:
                logger.warning("[LLM] 最终回复为空（含仅思维标签），按空响应处理")
                return None, used_tool
            return content, used_tool

        content = message.content
        return (_strip_think_tags(content) if content else None), False

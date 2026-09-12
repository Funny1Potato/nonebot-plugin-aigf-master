"""AI 生图客户端（OpenAI 兼容 images API）"""

import httpx
from openai import AsyncOpenAI


class ImageGenClient:
    """AI 生图客户端：调用 OpenAI 兼容的 images API（POST /v1/images/generations）"""

    def __init__(self, api_key: str, model: str, base_url: str,
                 proxy: str | None = None, timeout: int = 120):
        http_client = httpx.AsyncClient(proxy=proxy) if proxy else None
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        self._model = model
        self._timeout = timeout

    async def generate(self, prompt: str, size: str = "1024x1024") -> dict | None:
        """生成图片

        返回 {"b64": "..."}（base64 图片数据）或 {"url": "..."}（网络地址）；
        无结果返回 None。异常由调用方捕获处理。
        """
        response = await self._client.images.generate(
            model=self._model,
            prompt=prompt,
            n=1,
            size=size,
            response_format="b64_json",
            timeout=self._timeout,
        )
        if not response.data:
            return None
        data = response.data[0]
        if getattr(data, "b64_json", None):
            return {"b64": data.b64_json}
        if getattr(data, "url", None):
            return {"url": data.url}
        return None
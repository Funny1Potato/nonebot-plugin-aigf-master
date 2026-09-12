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

    async def generate(self, prompt: str, size: str = "1024x1024",
                       reference_images: list[str] | None = None,
                       watermark: bool = False) -> dict | None:
        """生成图片

        reference_images: 可选参考图列表（data URI：data:image/<小写格式>;base64,<b64>，或可访问 url）。
        有参考图时经 extra_body.image 传给豆包类服务做图生图/改绘；无参考图走普通文生图。

        返回 {"b64": "..."}（base64 图片数据）或 {"url": "..."}（网络地址）；
        无结果返回 None。异常由调用方捕获处理。
        """
        kwargs: dict = dict(
            model=self._model, prompt=prompt, n=1, size=size,
            response_format="b64_json", timeout=self._timeout,
        )
        if reference_images:
            kwargs["extra_body"] = {"image": reference_images, "watermark": watermark}
        response = await self._client.images.generate(**kwargs)
        if not response.data:
            return None
        data = response.data[0]
        if getattr(data, "b64_json", None):
            return {"b64": data.b64_json}
        if getattr(data, "url", None):
            return {"url": data.url}
        return None
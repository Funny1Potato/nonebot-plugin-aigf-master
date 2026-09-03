"""VLM（视觉语言模型）客户端"""

from openai import AsyncOpenAI


class VLMClient:
    """视觉语言模型客户端"""

    def __init__(self, api_key: str, model: str, base_url: str,
                 timeout: int = 60, http_client=None):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        self._model = model
        self._timeout = timeout

    async def request(self, prompt: str, image_base64: str, image_format: str) -> str | None:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/{image_format};base64,{image_base64}"}},
                {"type": "text", "text": prompt},
            ]}],
            timeout=self._timeout,
        )
        return response.choices[0].message.content

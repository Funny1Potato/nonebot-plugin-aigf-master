"""搜索客户端"""

from abc import ABC, abstractmethod

import httpx
from nonebot import logger


class SearchClient(ABC):
    """搜索客户端基类"""

    @abstractmethod
    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        ...

    def format_results(self, results: list[dict]) -> str:
        if not results:
            return "未找到相关搜索结果"
        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(f"{i}. {r.get('title', '')}\n   {r.get('content', '')}\n   来源: {r.get('url', '')}")
        return "\n\n".join(formatted)


class TavilySearchClient(SearchClient):
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from tavily import AsyncTavilyClient
            self._client = AsyncTavilyClient(api_key=self._api_key)
        return self._client

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            client = await self._get_client()
            response = await client.search(query=query, max_results=max_results, search_depth="basic")
            results = [{"title": r.get("title", ""), "content": r.get("content", ""), "url": r.get("url", "")}
                    for r in response.get("results", [])]
            return results
        except Exception as e:
            logger.error(f"[搜索] Tavily 失败: {e}")
            return []


class BochaSearchClient(SearchClient):
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._endpoint = "https://api.bochaai.com/v1/web-search"

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._endpoint, headers=headers,
                                         json={"query": query, "count": max_results, "summary": False})
                resp.raise_for_status()
                data = resp.json()
            results = [{"title": r.get("name", ""), "content": r.get("snippet", ""), "url": r.get("url", "")}
                    for r in data.get("data", {}).get("webPages", {}).get("value", [])]
            return results
        except Exception as e:
            logger.error(f"[搜索] Bocha 失败: {e}")
            return []


class BingSearchClient(SearchClient):
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._endpoint = "https://api.bing.microsoft.com/v7.0/search"

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            headers = {"Ocp-Apim-Subscription-Key": self._api_key}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._endpoint, headers=headers,
                                        params={"q": query, "count": max_results})
                resp.raise_for_status()
                data = resp.json()
            results = [{"title": r.get("name", ""), "content": r.get("snippet", ""), "url": r.get("url", "")}
                    for r in data.get("webPages", {}).get("value", [])]
            return results
        except Exception as e:
            logger.error(f"[搜索] Bing 失败: {e}")
            return []


class OpenWebSearchClient(SearchClient):
    """open-websearch 本地搜索服务（Node.js daemon，POST /search）"""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            payload = {"query": query, "limit": max_results}
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(f"{self._base_url}/search", json=payload)
                resp.raise_for_status()
                data = resp.json()
            if data.get("status") != "ok":
                logger.warning(f"[搜索] openwebsearch 返回异常: status={data.get('status')}, "
                               f"error={data.get('error')}, hint={data.get('hint')}")
                return []
            results = [{"title": r.get("title", ""), "content": r.get("description", ""), "url": r.get("url", "")}
                       for r in data.get("data", {}).get("results", [])]
            return results
        except Exception as e:
            logger.error(f"[搜索] openwebsearch 失败: {e}")
            return []


class DeepSeekSearchClient(SearchClient):
    """DeepSeek 联网搜索（POST {base}/v1/tools/search）

    文档：https://api-docs.deepseek.com/zh-cn/api/search
    - 按**成功请求次数**计费（不按 token、不按条数），`results` 为空数组不算成功、不计费
    - `summary` 是给模型看的长摘要，`snippet` 是短摘要；`content` 只有传 `need_content`
      才会带网页正文（默认不带，省掉额外抓取）
    - QPS 100；401 = Key 无效，402 = 余额不足
    """

    def __init__(self, api_key: str, base_url: str = ""):
        self._api_key = api_key
        self._base_url = (base_url or "https://api.deepseek.com").rstrip("/")

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
            # count 取值范围 1-50，越界会被判 400
            payload = {"query": query, "count": max(1, min(int(max_results or 1), 50))}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{self._base_url}/v1/tools/search", headers=headers, json=payload)
                if resp.status_code != 200:
                    self._log_failure(resp)
                    return []
                data = resp.json()
            results = []
            for r in data.get("results", []):
                content = (r.get("summary") or r.get("snippet") or "").strip()
                results.append({"title": r.get("title", ""), "content": content, "url": r.get("url", "")})
            return results
        except Exception as e:
            logger.error(f"[搜索] DeepSeek 失败: {e}")
            return []

    def _log_failure(self, resp):
        """DeepSeek 的错误体是 OpenAI 兼容格式，挑出人能看懂的说明"""
        try:
            err = (resp.json() or {}).get("error", {}) or {}
            msg = err.get("message") or ""
        except Exception:
            msg = ""
        hint = {
            400: "请求参数不合法",
            401: "API Key 无效",
            402: "余额不足",
            429: "触发 QPS 限制",
            500: "服务端错误",
            504: "下游超时",
        }.get(resp.status_code, "")
        logger.error(f"[搜索] DeepSeek 失败: HTTP {resp.status_code}"
                     + (f"（{hint}）" if hint else "")
                     + (f" {msg}" if msg else ""))


def create_search_client(api: str, api_key: str, base_url: str = "",
                         api_base_url: str = "") -> SearchClient | None:
    api = api.lower()
    if api == "tavily":
        return TavilySearchClient(api_key) if api_key else None
    elif api == "bocha":
        return BochaSearchClient(api_key) if api_key else None
    elif api == "bing":
        return BingSearchClient(api_key) if api_key else None
    elif api == "openwebsearch":
        return OpenWebSearchClient(base_url) if base_url else None
    elif api == "deepseek":
        return DeepSeekSearchClient(api_key, api_base_url) if api_key else None
    return None

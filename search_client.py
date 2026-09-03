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
                return []
            results = [{"title": r.get("title", ""), "content": r.get("description", ""), "url": r.get("url", "")}
                       for r in data.get("data", {}).get("results", [])]
            return results
        except Exception as e:
            logger.error(f"[搜索] openwebsearch 失败: {e}")
            return []


def create_search_client(api: str, api_key: str, base_url: str = "") -> SearchClient | None:
    api = api.lower()
    if api == "tavily":
        return TavilySearchClient(api_key) if api_key else None
    elif api == "bocha":
        return BochaSearchClient(api_key) if api_key else None
    elif api == "bing":
        return BingSearchClient(api_key) if api_key else None
    elif api == "openwebsearch":
        return OpenWebSearchClient(base_url) if base_url else None
    return None

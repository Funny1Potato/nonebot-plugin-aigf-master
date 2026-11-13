"""跨 bot HTTP 客户端：远程调用其它 bot 的插件"""

import httpx
from nonebot import logger

from .invocation import DeliveryResult


class PeerClient:
    """向其它 bot 发起远程插件调用（结果由对方钩子推回，本客户端只发调用请求）"""

    def __init__(self, peers: list[dict]):
        # peers: [{"name", "port", "token"}]
        self._peers = {p.get("name"): p for p in peers if p.get("name") and p.get("port")}

    def get(self, name: str) -> dict | None:
        return self._peers.get(name)

    def names(self) -> list[str]:
        return list(self._peers.keys())

    async def invoke(self, peer_name: str, command: str, group_id: int, user_id: int = 0,
                     parts: list[dict] | None = None, at_user_id: int = 0,
                     timeout: float = 30.0, sender_name: str = "", session: str = "") -> str:
        """Compatibility wrapper returning the acknowledgement text."""
        result = await self.invoke_detailed(peer_name, command, group_id, user_id,
                                            parts, at_user_id, timeout, sender_name, session)
        return result.message

    async def invoke_detailed(self, peer_name: str, command: str, group_id: int,
                              user_id: int = 0, parts: list[dict] | None = None,
                              at_user_id: int = 0, timeout: float = 30.0,
                              sender_name: str = "", session: str = "") -> DeliveryResult:
        """HTTP acknowledgement only; peer execution and output arrive asynchronously."""
        cfg = self._peers.get(peer_name)
        if not cfg:
            return DeliveryResult("not_sent", f"未知 bot: {peer_name}，命令未投递")
        url = f"http://127.0.0.1:{cfg['port']}/peer/invoke"
        headers = {"Authorization": f"Bearer {cfg.get('token', '')}"}
        payload = {"command": command, "group_id": group_id, "user_id": user_id,
                   "parts": parts or [], "at_user_id": at_user_id, "name": sender_name,
                   "session": session}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
                if not isinstance(body, dict) or body.get("ok") is not True:
                    logger.warning(f"[Peer] {peer_name} 回执无有效接收确认: {command}")
                    return DeliveryResult("uncertain", f"{peer_name} 未返回有效接收确认，命令可能已接收；结果不确定，请勿自动重试")
                logger.info(f"[Peer] {peer_name} 已接收调用请求（等待执行结果）: {command}")
                return DeliveryResult("accepted", f"{peer_name} 已接收调用请求，尚未确认命令执行；后续可能收到 [{peer_name}] 的响应，请勿因暂未响应而重试")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            logger.warning(f"[Peer] 未连接到 {peer_name}: {type(e).__name__}")
            return DeliveryResult("not_sent", f"未能连接到 {peer_name}，命令未投递")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if 400 <= status < 500 and status not in (408,):
                logger.warning(f"[Peer] {peer_name} 拒绝请求: HTTP {status}")
                return DeliveryResult("not_sent", f"{peer_name} 拒绝请求（HTTP {status}），命令未被接收")
            logger.warning(f"[Peer] {peer_name} 返回 HTTP {status}，接收状态不确定")
            return DeliveryResult("uncertain", f"{peer_name} 返回 HTTP {status}，命令接收状态不确定，请勿自动重试")
        except Exception as e:
            logger.warning(f"[Peer] {peer_name} 回执异常: {type(e).__name__}")
            return DeliveryResult("uncertain", f"{peer_name} 的调用回执异常，命令可能已接收；结果不确定，请勿自动重试")

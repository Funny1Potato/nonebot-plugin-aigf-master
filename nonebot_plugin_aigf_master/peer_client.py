"""跨 bot HTTP 客户端：远程调用其它 bot 的插件"""

import httpx
import asyncio
import time
from collections import OrderedDict
from uuid import uuid4
from nonebot import logger

from .invocation import DeliveryResult


class PeerClient:
    """向其它 bot 发起远程插件调用（结果由对方钩子推回，本客户端只发调用请求）"""

    def __init__(self, peers: list[dict]):
        # peers: [{"name", "port", "token"}]
        self._peers = {p.get("name"): p for p in peers if p.get("name") and p.get("port")}
        self.requests = OrderedDict()

    def observe_output(self, peer, session, request_id):
        row = self.requests.get(request_id)
        if row is None or row["peer"] != peer or row["session"] != session:
            return False
        row["outputs"] += 1
        row["state"] = "output_received"
        return True

    def status(self, session):
        labels = {"sending": "投递中", "accepted": "已接收，等待输出", "uncertain": "接收不确定",
                  "not_sent": "未投递", "output_received": "收到输出（不代表执行完成）", "output_timeout": "等待输出超时（不重发）"}
        rows = [r for r in self.requests.values() if r["session"] == session][-5:]
        lines = ["Peer 请求（最近 5 条，重启清空）："]
        for row in rows:
            state = row["state"]
            if state in {"accepted", "uncertain"} and time.monotonic() >= row["deadline"]:
                state = "output_timeout"
            lines.append(f"{row['id']} [{row['peer']}] {labels[state]}，输出段 {row['outputs']}")
        return "\n".join(lines) if rows else "Peer 请求：暂无"

    def _delivery(self, request_id, status, message):
        row = self.requests.get(request_id)
        if row and not row["outputs"]:
            row["state"] = status
            row["deadline"] = time.monotonic() + row["timeout"]
        return DeliveryResult(status, message)

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
                              sender_name: str = "", session: str = "", request_id: str = "",
                              output_timeout: float = 60.0) -> DeliveryResult:
        """HTTP acknowledgement only; peer execution and output arrive asynchronously."""
        cfg = self._peers.get(peer_name)
        if not cfg:
            return DeliveryResult("not_sent", f"未知 bot: {peer_name}，命令未投递")
        request_id = request_id or uuid4().hex
        if request_id in self.requests:
            return DeliveryResult("uncertain", "该请求编号已使用，不重复投递")
        self.requests[request_id] = {"id": request_id, "peer": peer_name, "session": session,
            "state": "sending", "outputs": 0, "timeout": output_timeout,
            "deadline": time.monotonic() + output_timeout}
        while len(self.requests) > 200:
            self.requests.popitem(last=False)
        url = f"http://127.0.0.1:{cfg['port']}/peer/invoke"
        headers = {"Authorization": f"Bearer {cfg.get('token', '')}"}
        payload = {"command": command, "group_id": group_id, "user_id": user_id,
                   "parts": parts or [], "at_user_id": at_user_id, "name": sender_name,
                   "session": session, "request_id": request_id}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
                if not isinstance(body, dict) or body.get("ok") is not True:
                    logger.warning(f"[Peer] {peer_name} 回执无有效接收确认: {command}")
                    return self._delivery(request_id, "uncertain", f"{peer_name} 未返回有效接收确认，命令可能已接收；结果不确定，请勿自动重试")
                if body.get("request_id") not in (None, "", request_id):
                    return self._delivery(request_id, "uncertain", "peer 回执编号不匹配；结果不确定，不自动重试")
                logger.info(f"[Peer] {peer_name} 已接收调用请求（等待执行结果）: {command}")
                return self._delivery(request_id, "accepted", f"{peer_name} 已接收调用请求，尚未确认命令执行；后续可能收到 [{peer_name}] 的响应，请勿因暂未响应而重试")
        except asyncio.CancelledError:
            self._delivery(request_id, "uncertain", "调用被取消，接收状态不确定，不自动重试")
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            logger.warning(f"[Peer] 未连接到 {peer_name}: {type(e).__name__}")
            return self._delivery(request_id, "not_sent", f"未能连接到 {peer_name}，命令未投递")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if 400 <= status < 500 and status not in (408,):
                logger.warning(f"[Peer] {peer_name} 拒绝请求: HTTP {status}")
                return self._delivery(request_id, "not_sent", f"{peer_name} 拒绝请求（HTTP {status}），命令未被接收")
            logger.warning(f"[Peer] {peer_name} 返回 HTTP {status}，接收状态不确定")
            return self._delivery(request_id, "uncertain", f"{peer_name} 返回 HTTP {status}，命令接收状态不确定，请勿自动重试")
        except Exception as e:
            logger.warning(f"[Peer] {peer_name} 回执异常: {type(e).__name__}")
            return self._delivery(request_id, "uncertain", f"{peer_name} 的调用回执异常，命令可能已接收；结果不确定，请勿自动重试")

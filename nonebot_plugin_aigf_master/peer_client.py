"""跨 bot HTTP 客户端：远程调用其它 bot 的插件"""

import httpx
from nonebot import logger


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
                     at_qq: int = 0, timeout: float = 30.0) -> str:
        """远程调用其它 bot 的插件命令，返回确认信息

        插件执行结果由对方 on_calling_api 钩子推送到本 bot 的 /peer/capture，
        因此这里只负责发出调用请求并返回执行确认。
        """
        cfg = self._peers.get(peer_name)
        if not cfg:
            return f"未知 bot: {peer_name}"
        url = f"http://127.0.0.1:{cfg['port']}/peer/invoke"
        headers = {"Authorization": f"Bearer {cfg.get('token', '')}"}
        payload = {"command": command, "group_id": group_id, "user_id": user_id, "at_user_id": at_qq}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                logger.success(f"[Peer] 调用 {peer_name} 成功: {command}")
                return (f"命令已投递给 {peer_name}。只有后续聊天记录里出现 [{peer_name}] 的响应才算执行成功；"
                        "一直没有响应说明该命令不存在或对方未处理，不要当作已完成")
        except Exception as e:
            logger.error(f"[Peer] 调用 {peer_name} 失败: {e}")
            return f"远程调用 {peer_name} 失败: {e}"

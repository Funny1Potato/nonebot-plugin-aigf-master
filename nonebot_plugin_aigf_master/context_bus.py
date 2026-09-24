"""跨插件消息上下文总线"""

from collections import deque

from nonebot import logger

from .models import PluginMessage


class ContextBus:
    """每会话独立的跨插件消息缓冲区（键为会话键 `适配器:会话id`）"""

    def __init__(self, max_per_group: int = 50):
        self._max = max_per_group
        self._buffers: dict[str, deque[PluginMessage]] = {}

    def push(self, msg: PluginMessage):
        if msg.session_key not in self._buffers:
            self._buffers[msg.session_key] = deque(maxlen=self._max)
        self._buffers[msg.session_key].append(msg)
        logger.debug(f"[ContextBus] 捕获: [{msg.source_plugin}] {msg.content[:50]}")

    def get_recent(self, session_key: str, limit: int = 20) -> list[PluginMessage]:
        buf = self._buffers.get(session_key)
        if not buf:
            return []
        return list(buf)[-limit:]

    def clear(self, session_key: str):
        self._buffers.pop(session_key, None)
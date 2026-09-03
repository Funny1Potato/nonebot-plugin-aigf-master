"""跨插件消息上下文总线"""

from collections import deque

from nonebot import logger

from .models import PluginMessage


class ContextBus:
    """每群独立的跨插件消息缓冲区"""

    def __init__(self, max_per_group: int = 50):
        self._max = max_per_group
        self._buffers: dict[int, deque[PluginMessage]] = {}

    def push(self, msg: PluginMessage):
        if msg.group_id not in self._buffers:
            self._buffers[msg.group_id] = deque(maxlen=self._max)
        self._buffers[msg.group_id].append(msg)
        logger.debug(f"[ContextBus] 捕获: [{msg.source_plugin}] {msg.content[:50]}")

    def get_recent(self, group_id: int, limit: int = 20) -> list[PluginMessage]:
        buf = self._buffers.get(group_id)
        if not buf:
            return []
        return list(buf)[-limit:]

    def clear(self, group_id: int):
        self._buffers.pop(group_id, None)

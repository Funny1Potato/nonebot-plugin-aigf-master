"""插件调用器：通过 synthetic event 调用本实例其它插件"""

import asyncio
from datetime import datetime

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.message import handle_event


def _apply_command_prefix(command: str) -> str:
    """按本 bot 配置的命令前缀（COMMAND_START）补充到命令前"""
    try:
        command_start = get_driver().config.command_start
        if command_start:
            prefix = next(iter(command_start), "")
            if prefix and not command.startswith(prefix):
                command = prefix + command
    except Exception:
        pass
    return command


class PluginInvoker:
    """调用本实例的其它插件并捕获响应"""

    def __init__(self):
        self._active = False
        self._captured: list[dict] = []
        self._target_group: int | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    async def invoke(self, bot, group_id: int, command: str, timeout: float = 30.0,
                     user_id: int = 0) -> list[dict]:
        """调用插件命令，返回捕获的消息列表
        
        Args:
            user_id: 触发命令的用户 QQ 号，用于需要读取发送者信息的命令
        """
        logger.debug(f"[Invoker] 创建 synthetic event: command={command}, group={group_id}, user={user_id}")
        self._active = True
        self._captured = []
        self._target_group = group_id

        try:
            event = self._create_synthetic_event(bot, group_id, command, user_id)
            await asyncio.wait_for(handle_event(bot, event), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[Invoker] 调用超时: {command}")
        except Exception as e:
            logger.error(f"[Invoker] 调用失败: {e}")
        finally:
            self._active = False

        return self._captured

    def capture_if_active(self, group_id: int, data: dict):
        """由 api_hooks 的 on_calling_api 调用"""
        if self._active and group_id == self._target_group:
            self._captured.append(data)

    @staticmethod
    def _create_synthetic_event(bot, group_id: int, command: str, user_id: int = 0) -> GroupMessageEvent:
        """创建模拟的群消息事件"""
        # LLM 传无前缀命令，这里按本 bot 配置的命令前缀补充
        command = _apply_command_prefix(command)

        message = Message(MessageSegment.text(command))
        now = datetime.now()
        return GroupMessageEvent(
            time=int(now.timestamp()),
            self_id=int(bot.self_id) if bot else 0,
            post_type="message",
            sub_type="normal",
            message_type="group",
            user_id=user_id,
            group_id=group_id,
            message_id=0,
            message=message,
            raw_message=command,
            font=0,
            sender={"user_id": user_id, "nickname": "aigf_user", "role": "member"},
        )

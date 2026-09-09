"""插件调用器：通过 synthetic event 调用本实例其它插件"""

import asyncio
from datetime import datetime
from typing import Literal

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.message import handle_event

# nonebot 的 Event 基类允许额外字段（ConfigDict(extra="allow")），用它标记合成事件，
# 不碰任何插件会读取的协议字段
SYNTHETIC_FLAG = "aigf_synthetic"

InvokeResult = Literal["ok", "timeout", "error"]


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
    """调用本实例的其它插件

    插件的响应不在这里收集：它由 api_hooks 的 on_calling_api 钩子写入消息缓冲区，
    下一批处理时 LLM 自然看到。本类只负责投递命令并报告投递结果，
    因此不含跨群共享的可变状态。
    """

    def __init__(self):
        # {id(event): event}，强引用避免 id 被复用
        self._synthetic: dict[int, object] = {}

    def is_synthetic(self, event) -> bool:
        """判断事件是否由本调用器合成（供 auto_chat 精确跳过，不误伤真实用户消息）"""
        return id(event) in self._synthetic or bool(getattr(event, SYNTHETIC_FLAG, False))

    async def invoke(self, bot, group_id: int, command: str, timeout: float = 30.0,
                     user_id: int = 0, at_qq: int = 0) -> InvokeResult:
        """投递命令并等待分发结束

        Args:
            user_id: 触发命令的用户 QQ 号，用于需要读取发送者信息的命令
            at_qq: 命令要 @ 的群友 QQ 号（0 表示不 @），如"决斗 @张三"这类命令的目标

        返回投递结果，不代表插件是否回复。超时只取消本次分发，
        插件稍后的输出仍会经钩子进入消息缓冲区。
        """
        logger.debug(f"[Invoker] 创建 synthetic event: command={command}, group={group_id}, user={user_id}, at={at_qq}")
        event = None

        try:
            event = self._create_synthetic_event(bot, group_id, command, user_id, at_qq)
            self._synthetic[id(event)] = event
            await asyncio.wait_for(handle_event(bot, event), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[Invoker] 调用超时: {command}")
            return "timeout"
        except Exception as e:
            logger.error(f"[Invoker] 调用失败: {e}")
            return "error"
        finally:
            if event is not None:
                self._synthetic.pop(id(event), None)

        return "ok"

    @staticmethod
    def _create_synthetic_event(bot, group_id: int, command: str, user_id: int = 0,
                                at_qq: int = 0) -> GroupMessageEvent:
        """创建模拟的群消息事件"""
        # LLM 传无前缀命令，这里按本 bot 配置的命令前缀补充
        command = _apply_command_prefix(command)

        # 需要 @ 群友时，at 段放在命令文本之后（on_command 匹配命令头不受影响，
        # 依赖 at 段的插件 get_message() 可见，get_plaintext() 得到"命令 @qq"）
        if at_qq:
            message = Message([MessageSegment.text(command), MessageSegment.at(at_qq)])
        else:
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
            **{SYNTHETIC_FLAG: True},
        )

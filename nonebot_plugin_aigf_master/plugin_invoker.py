"""插件调用器：通过「复制本会话最近一条真实事件」调用本实例其它插件（跨适配器）

合成事件不再手搓 OneBot 的 `GroupMessageEvent`，而是取该会话最近一条真实消息事件，
用 `model_copy` 换掉消息体后投递——这样消息类型、适配器字段、发送者信息都自然正确，
对任意适配器都成立（不依赖任何 onebot 专属类型）。
"""

import asyncio
from typing import Literal

from nonebot import get_driver, logger
from nonebot.message import handle_event
from nonebot_plugin_uninfo import get_interface

from .session import SessionInfo, display_name
from .unimsg import compose_unimsg_list, plain_text

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


def _coerce_like(template_value, value):
    """把新的用户 id 转成模板事件里同类型（多数适配器是 int，部分是 str）"""
    if isinstance(template_value, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return template_value
    return str(value)


def _refresh_uniseg_cache(event, bot) -> None:
    """刷新 alconna 按 message_id 缓存的消息

    合成事件复用了原消息的 message_id，而 alconna 会把「收到的消息」按 message_id 缓存
    （`extension.py` 里的 unimsg_cache，默认开启），命中缓存时响应器读到的是**原来那条用户消息**，
    于是 alconna 写的目标插件永远匹配不到我们投递的命令。这里把该 id 的缓存改成合成后的消息。
    属于对方库的内部缓存，拿不到就静默跳过（不影响 onebot 原生插件）。
    """
    try:
        from nonebot_plugin_alconna import UniMessage, get_message_id
        from nonebot_plugin_alconna.extension import unimsg_cache, unimsg_origin_cache
    except Exception:
        return
    try:
        msg_id = get_message_id(event, bot)
        message = event.get_message()
    except Exception as e:
        logger.debug(f"[Invoker] 取消息 id 失败，跳过缓存刷新: {e}")
        return
    try:
        unimsg = UniMessage.of(message, bot=bot)
    except Exception as e:
        logger.debug(f"[Invoker] 刷新 alconna 消息缓存失败: {e}")
        return
    for cache in (unimsg_cache, unimsg_origin_cache):
        try:
            cache[msg_id] = unimsg
        except Exception:
            pass


async def _sender_update(bot, session: SessionInfo | None, user_id) -> dict:
    """按用户 id 取群成员信息，用于同步合成事件里的发送者昵称/角色/群名片"""
    update: dict = {}
    interface = get_interface(bot)
    if not (interface and session and session.member_scene_type is not None and session.member_scene_id):
        return update
    try:
        member = await interface.get_member(session.member_scene_type, session.member_scene_id, str(user_id))
    except Exception as e:
        logger.debug(f"[Invoker] 取成员信息失败: {e}")
        return update
    if member is None:
        return update
    if display_name(member.user):
        update["nickname"] = display_name(member.user)
    card = (member.nick or member.user.nick or "").strip()
    if card:
        update["card"] = card
    # uninfo 的 Role.name 保留适配器原始角色名（onebot: owner/admin/member）
    if member.role is not None and member.role.name:
        update["role"] = member.role.name.lower()
    return update


class PluginInvoker:
    """调用本实例的其它插件

    插件的响应不在这里收集：它由 api_hooks 的 on_calling_api 钩子写入消息缓冲区，
    下一批处理时 LLM 自然看到。本类只负责投递命令并报告投递结果，
    因此不含跨会话共享的可变状态。
    """

    def __init__(self):
        # {id(event): event}，强引用避免 id 被复用
        self._synthetic: dict[int, object] = {}

    def is_synthetic(self, event) -> bool:
        """判断事件是否由本调用器合成（供 auto_chat 精确跳过，不误伤真实用户消息）"""
        return id(event) in self._synthetic or bool(getattr(event, SYNTHETIC_FLAG, False))

    async def invoke(self, bot, session: SessionInfo | None, template_event, command: str,
                     timeout: float = 30.0, user_id=None,
                     parts: list[dict] | None = None) -> InvokeResult:
        """投递命令并等待分发结束

        Args:
            session: 会话信息（@ 解析、成员信息用）
            template_event: 该会话最近一条真实消息事件，用作合成事件的模板
            user_id: 触发命令的用户 id；默认沿用模板事件的发送者
            parts: 命令参数段（与回复的 reply 字段同结构），如 [{"type":"at","target":12345}]；
                   条目之间会以空格拼在命令之后

        返回投递结果，不代表插件是否回复。超时只取消本次分发，
        插件稍后的输出仍会经钩子进入消息缓冲区。
        """
        if template_event is None:
            logger.warning("[Invoker] 该会话没有可复制的真实事件，无法构造调用事件")
            return "error"
        try:
            event = await self._build_event(bot, session, template_event, command, user_id, parts)
        except Exception as e:
            logger.error(f"[Invoker] 构造调用事件失败: {e}")
            return "error"

        self._synthetic[id(event)] = event
        try:
            await asyncio.wait_for(handle_event(bot, event), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[Invoker] 调用超时: {command}")
            return "timeout"
        except Exception as e:
            logger.error(f"[Invoker] 调用失败: {e}")
            return "error"
        finally:
            self._synthetic.pop(id(event), None)

        return "ok"

    @staticmethod
    async def _build_event(bot, session: SessionInfo | None, template, command: str,
                           user_id=None, parts: list[dict] | None = None):
        """复制真实事件并换掉消息体（消息拼装与回复发送共用 compose_unimsg_list）"""
        # LLM 传无前缀命令，这里按本 bot 配置的命令前缀补充
        command = _apply_command_prefix(command)
        messages, _ = await compose_unimsg_list(
            bot, session, [{"type": "text", "content": command}] + list(parts or []))
        unimsg = messages[0] if messages else None
        message = await unimsg.export(bot=bot) if unimsg is not None else bot.adapter.get_message_class()()

        update: dict = {"message": message}
        if hasattr(template, "raw_message"):
            update["raw_message"] = plain_text(unimsg) if unimsg is not None else command
        # 部分适配器（onebot 等）另有 original_message（@ 机器人时保留的原文），一并换掉，
        # 否则 alconna 的 use_origin 路径仍会读到原来的用户消息
        if hasattr(template, "original_message"):
            update["original_message"] = message
        # 合成消息没有引用关系：清掉被复制消息的 reply，避免目标插件以为它在回复某条消息
        if hasattr(template, "reply"):
            update["reply"] = None

        if user_id not in (None, "", 0, "0"):
            update["user_id"] = _coerce_like(getattr(template, "user_id", None), user_id)
            sender = getattr(template, "sender", None)
            if sender is not None:
                sender_update = {"user_id": update["user_id"]}
                sender_update.update(await _sender_update(bot, session, user_id))
                try:
                    update["sender"] = sender.model_copy(update=sender_update)
                except Exception as e:
                    logger.debug(f"[Invoker] 同步发送者信息失败: {e}")

        event = template.model_copy(update={**update, SYNTHETIC_FLAG: True})
        if not getattr(event, SYNTHETIC_FLAG, False):
            try:
                setattr(event, SYNTHETIC_FLAG, True)
            except Exception:
                pass
        _refresh_uniseg_cache(event, bot)
        logger.debug(f"[Invoker] 创建 synthetic event: command={command}, session="
                     f"{getattr(session, 'key', '')}, user={user_id}, parts={parts}")
        return event
"""插件调用器：通过「复制本会话最近一条真实事件」调用本实例其它插件（跨适配器）

合成事件不再手搓 OneBot 的 `GroupMessageEvent`，而是取该会话最近一条真实消息事件，
用 `model_copy` 换掉消息体后投递——这样消息类型、适配器字段、发送者信息都自然正确，
对任意适配器都成立（不依赖任何 onebot 专属类型）。
"""

import asyncio
from copy import deepcopy
from typing import Literal
from collections.abc import MutableMapping

from nonebot import get_driver, logger
from nonebot.message import handle_event
from nonebot_plugin_alconna import UniMessage
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


def _can_hold_message(template, message, expected_text: str) -> bool:
    """事件的 `message` 字段能否直接放本适配器的 Message

    多数适配器（onebot/telegram/discord…）可以；但 **Satori** 这类事件的 `message` 是 `{id, content}`
    结构体，真正的消息由 `get_message()` 解析出来——硬塞 Message 进去会把事件弄坏
    （`get_message()` 报错、`get_message_id()` 取不到 id），目标插件也就读不到命令。

    做法是**实测**：塞进副本后能取到非空 `get_message()` 就认（拿不准时保留原行为、由文本替换兜底）。
    """
    try:
        probe = template.model_copy(update={"message": message})
        text = str(probe.get_message())
    except Exception as e:
        logger.debug(f"[Invoker] message 字段不接受统一消息，改用文本替换: {e}")
        return False
    if not text.strip():
        return False
    # 能读到新内容才认为放得下：纯文本比对（at/图片段在 extract 里看不到）或整串比对
    return expected_text.strip() in text or str(message) in text


def _plain_incoming(bot, event) -> str:
    """取事件里原来的纯文本（用于「把旧文本替换成命令文本」）

    先用 alconna 的通用层解析（各适配器都可靠），拿不到再退回适配器自己的 `extract_plain_text()`
    —— 部分适配器的 `extract_plain_text()` 会返回空串（实测 Satori），直接用它会让替换失效。
    """
    try:
        unimsg = UniMessage.of(event.get_message(), bot=bot)
        text = unimsg.extract_plain_text().strip()
        if not text:
            # 个别适配器（Satori）的 extract_plain_text 取不到文本，直接拼 Text 段
            text = "".join(str(getattr(seg, "text", "") or "") for seg in unimsg).strip()
        if text:
            return text
    except Exception as e:
        logger.debug(f"[Invoker] 通用层取原文失败: {e}")
    try:
        return event.get_message().extract_plain_text().strip()
    except Exception:
        return ""


def _is_patchable(value) -> bool:
    """能继续往里找文本字段的容器：映射 / 列表 / pydantic 模型"""
    return (isinstance(value, (MutableMapping, list))
            or bool(getattr(type(value), "model_fields", None)))


def _patch_text_fields(event, old_text: str, new_text: str) -> None:
    """把事件里出现的旧消息文本替换成新文本（递归进嵌套模型/字典/列表，先复制再改以免污染原事件）

    消息文本不总在统一的 `message` 字段里：discord 用 `content`、dodo 用 `message_body`、
    feishu 在嵌套的 `event.event.message.content`、**Satori 的 `message` 是 `{id, content}` 结构体**。
    只替换 `message` 的话，目标插件 `get_message()` 读到的仍是原来那条用户消息，命令永远匹配不上。
    这里采用「旧文本出现在哪个字符串槽位就替换哪个」的通用兜底（不做适配器特判）。
    """
    if not old_text:
        return

    def visit(parent, name, value, depth: int, setter) -> None:
        if isinstance(value, str):
            if old_text in value:
                try:
                    setter(name, value.replace(old_text, new_text))
                except Exception as e:
                    logger.debug(f"[Invoker] 替换文本字段 {name} 失败: {e}")
            return
        if not _is_patchable(value):
            return
        if getattr(type(parent), "model_fields", None):
            # 从模型往下走时复制一份，避免改到用于下次调用的模板事件
            try:
                copied = deepcopy(value)
                setter(name, copied)
                value = copied
            except Exception:
                pass
        walk(value, depth + 1)

    def walk(obj, depth: int = 0) -> None:
        if depth > 4:
            return
        fields = list(getattr(type(obj), "model_fields", None) or [])
        if fields:
            for name in fields:
                try:
                    value = getattr(obj, name)
                except Exception:
                    continue
                visit(obj, name, value, depth, lambda n, v, o=obj: setattr(o, n, v))
        elif isinstance(obj, MutableMapping):
            for name in list(obj.keys()):
                visit(obj, name, obj.get(name), depth,
                      lambda n, v: obj.__setitem__(n, v))
        elif isinstance(obj, list):
            for item in obj:
                if _is_patchable(item):
                    walk(item, depth + 1)

    walk(event)


def _reset_message_cache(event) -> None:
    """清掉适配器「懒加载的消息缓存」私有属性（如 Discord 的 `_message` / `_original_message`）

    Discord 的 `get_message()` 是 `if not hasattr(self, "_message"): self._message = ...` —— 结果会
    缓存在私有属性里，而 `model_copy` 会把这份缓存一起带过来。不清理的话，即使我们换掉了消息体，
    目标插件调用 `get_message()` 读到的仍是**原来那条消息**，命令永远匹配不上。
    只动「名字里带 message 的私有属性」，不碰任何协议字段。
    """
    for holder in (getattr(event, "__dict__", None), getattr(event, "__pydantic_private__", None)):
        if not isinstance(holder, dict):
            continue
        stale = [k for k in holder
                 if k.startswith("_") and not k.startswith("__") and "message" in k.lower()]
        for key in stale:
            try:
                holder.pop(key, None)
            except Exception as e:
                logger.debug(f"[Invoker] 清理消息缓存 {key} 失败: {e}")


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

        update: dict = {}
        command_text = plain_text(unimsg) if unimsg is not None else command
        if _can_hold_message(template, message, command_text):
            update["message"] = message
            # 部分适配器（onebot 等）另有 original_message（@ 机器人时保留的原文），一并换掉，
            # 否则 alconna 的 use_origin 路径仍会读到原来的用户消息
            if hasattr(template, "original_message"):
                update["original_message"] = message
        if hasattr(template, "raw_message"):
            update["raw_message"] = command_text
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
        # 消息文本不总在 `message` 字段里（见 _patch_text_fields），补齐后才对得上目标插件
        old_text = _plain_incoming(bot, template)
        _patch_text_fields(event, old_text, plain_text(unimsg) if unimsg is not None else command)
        _reset_message_cache(event)
        _refresh_uniseg_cache(event, bot)
        logger.debug(f"[Invoker] 创建 synthetic event: command={command}, session="
                     f"{getattr(session, 'key', '')}, user={user_id}, parts={parts}")
        return event
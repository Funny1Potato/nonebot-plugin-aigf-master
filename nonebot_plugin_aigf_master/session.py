"""会话身份层：把各适配器的事件统一成「会话」（`适配器:会话id`）

会话信息优先走 nonebot_plugin_uninfo（用户/成员昵称、群名片、场景类型、场景名），
uninfo 不支持的适配器回落到 alconna 的 Target（只有 id，没有昵称），再不行返回 None
（调用方跳过该条消息，不报错）。旧版数据与配置里的裸 id 一律按 onebot11 识别。
"""

import re
from dataclasses import dataclass
from pathlib import Path

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import Target, get_target
from nonebot_plugin_uninfo import SceneType, get_interface, get_session

# alconna / uninfo 的 SupportAdapter 值就是适配器显示名（bot.adapter.get_name()）
_ADAPTER_ALIASES = {
    "OneBot V11": "onebot11",
    "OneBot V12": "onebot12",
}
# 旧数据（裸 id 的会话目录、裸 id 的启用清单项）一律按 onebot11 识别
LEGACY_SLUG = "onebot11"


def adapter_name_of(value) -> str:
    """适配器名归一：SupportAdapter 枚举在 Python 3.10 下 str() 是 'SupportAdapter.xxx'，要取 .value"""
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "")


def adapter_slug(name: str) -> str:
    """适配器显示名 → 会话键里的短标识（OneBot V11 → onebot11）"""
    name = adapter_name_of(name).strip()
    if name in _ADAPTER_ALIASES:
        return _ADAPTER_ALIASES[name]
    slug = re.sub(r"[^0-9a-z]+", "", name.lower())
    return slug or "unknown"


def session_key(slug: str, scene_path: str) -> str:
    """会话唯一键：`适配器:会话id`（如 onebot11:617770183）"""
    return f"{slug}:{scene_path}"


def disk_key(key: str) -> str:
    """会话键 → 可用作目录/文件名的形式（Windows 目录名不能含冒号）"""
    return key.replace(":", "_")


def split_session(value) -> tuple[str, str]:
    """把配置项里的会话项拆成 (slug, 会话id)；裸 id 视为 onebot11（兼容旧配置）"""
    text = str(value).strip()
    if ":" in text:
        slug, _, chat_id = text.partition(":")
        slug, chat_id = slug.strip(), chat_id.strip()
        if slug and chat_id:
            return slug, chat_id
    return LEGACY_SLUG, text


def normalize_enabled(values) -> set[str]:
    """启用清单（可含 `adapter:id` 与裸 id）→ 会话键集合"""
    return {session_key(*split_session(v)) for v in (values or []) if str(v).strip()}


def display_name(user) -> str:
    """uninfo 的 User/Member → 显示名（昵称优先，其次群名片，最后 id）"""
    if user is None:
        return ""
    for attr in ("name", "nick"):
        value = (getattr(user, attr, None) or "").strip()
        if value:
            return value
    return str(getattr(user, "id", "") or "")


@dataclass
class SessionInfo:
    """一个会话（群 / 频道 / 私聊）的身份信息"""
    key: str
    slug: str
    scene_path: str
    native_chat_id: str
    adapter_name: str = ""
    is_private: bool = False
    is_channel: bool = False
    scene_name: str = ""
    scene_type: SceneType | None = None
    member_scene_type: SceneType | None = None
    member_scene_id: str = ""
    user_id: str = ""
    user_name: str = ""
    card: str = ""
    target: Target | None = None

    @property
    def disk_key(self) -> str:
        return disk_key(self.key)

    @property
    def label(self) -> str:
        """日志/提示用的可读标签"""
        return self.key

    @property
    def numeric_chat_id(self) -> int:
        """原生会话 id 的数字形式（跨 bot 推送等只认数字的场合用）；非数字返回 0"""
        return int(self.native_chat_id) if self.native_chat_id.isdigit() else 0


def event_user_id(event: Event) -> str:
    """事件主体 id；通知类事件的 `get_user_id()` 常常直接抛 ValueError（OneBot V12 / Telegram / EFChat 等）"""
    try:
        return str(event.get_user_id() or "")
    except Exception:
        return ""


def _from_uninfo(bot: Bot, event: Event, sess) -> SessionInfo:
    scene = sess.scene
    member_scene = scene.parent or scene
    slug = adapter_slug(sess.adapter)
    user = sess.user
    member = sess.member
    card = (getattr(member, "nick", None) or getattr(user, "nick", None) or "").strip()
    try:
        target = get_target(event, bot)
    except Exception as e:
        logger.debug(f"[会话] 生成 Target 失败: {e}")
        target = None
    return SessionInfo(
        key=session_key(slug, sess.scene_path),
        slug=slug,
        scene_path=sess.scene_path,
        native_chat_id=str(scene.id),
        adapter_name=bot.adapter.get_name(),
        is_private=scene.is_private,
        is_channel=scene.is_channel,
        scene_name=(scene.name or "").strip(),
        scene_type=scene.type,
        member_scene_type=member_scene.type,
        member_scene_id=str(member_scene.id),
        user_id=event_user_id(event) or user.id or "",
        user_name=display_name(user),
        card=card,
        target=target,
    )


async def resolve_session(bot: Bot, event: Event) -> SessionInfo | None:
    """解析事件所属会话；适配器不支持时返回 None（该条消息跳过）"""
    try:
        sess = await get_session(bot, event)
    except Exception as e:
        logger.debug(f"[会话] uninfo 解析失败（{bot.adapter.get_name()}）: {e}")
        sess = None
    if sess is not None:
        try:
            info = _from_uninfo(bot, event, sess)
        except Exception as e:
            logger.debug(f"[会话] uninfo 会话结构异常: {e}")
        else:
            if info.user_name:
                return info
            logger.debug("[会话] uninfo 未提供昵称，回落 Target")
    # 回落：alconna Target（只有 id）
    try:
        target = get_target(event, bot)
    except Exception as e:
        logger.debug(f"[会话] {bot.adapter.get_name()} 无法解析会话: {e}")
        return None
    scene_path = f"{target.parent_id}_{target.id}" if (target.channel and target.parent_id) else target.id
    slug = adapter_slug(bot.adapter.get_name())
    return SessionInfo(
        key=session_key(slug, str(scene_path)),
        slug=slug,
        scene_path=str(scene_path),
        native_chat_id=str(target.id),
        adapter_name=bot.adapter.get_name(),
        is_private=bool(target.private),
        is_channel=bool(target.channel),
        user_id=event_user_id(event),
        target=target,
    )


def is_enabled(info: SessionInfo, config) -> bool:
    """会话是否在启用清单里（群/频道与私聊分开）"""
    values = config.aigfm_enabled_private if info.is_private else config.aigfm_enabled_groups
    return info.key in normalize_enabled(values)


async def display_user(bot: Bot, info: SessionInfo | None, user_id) -> str:
    """按用户 id 取显示名（uninfo 成员/用户信息）；取不到返回 id 本身"""
    uid = str(user_id or "").strip()
    if not uid:
        return ""
    interface = get_interface(bot)
    if interface and info is not None and info.member_scene_type is not None and info.member_scene_id:
        try:
            member = await interface.get_member(info.member_scene_type, info.member_scene_id, uid)
            if member is not None:
                return display_name(member.user) or uid
        except Exception as e:
            logger.debug(f"[会话] 取成员 {uid} 失败: {e}")
        try:
            user = await interface.get_user(uid)
            if user is not None:
                return display_name(user) or uid
        except Exception as e:
            logger.debug(f"[会话] 取用户 {uid} 失败: {e}")
    return uid


@dataclass(frozen=True)
class MemberName:
    """成员可用于 @ 匹配的一个名字（昵称或群名片）"""

    name: str
    user_id: str


async def list_member_names(bot: Bot, info: SessionInfo | None) -> list[MemberName]:
    """会话成员的可匹配名字列表（uninfo 成员列表优先，onebot 群成员列表回落）

    返回 [] 表示拿不到成员列表（私聊、适配器不支持、接口异常），调用方据此跳过文本 @ 转换。
    """
    results: list[MemberName] = []
    if info is not None and info.is_private:
        return results
    try:
        interface = get_interface(bot)
    except Exception as e:      # 极少数 Bot 实现没有 adapter（测试 mock / 非常规封装）
        logger.debug(f"[会话] 取 uninfo 接口失败: {e}")
        interface = None
    if interface and info is not None and info.member_scene_type is not None and info.member_scene_id:
        try:
            for member in await interface.get_members(info.member_scene_type, info.member_scene_id):
                uid = str(member.user.id)
                for name in (display_name(member.user), (member.nick or "").strip()):
                    if name:
                        results.append(MemberName(name, uid))
            if results:
                return results
        except Exception as e:
            logger.debug(f"[会话] 成员列表匹配失败: {e}")
    # onebot 回落：部分适配器的成员列表未实现 uninfo 查询接口
    chat_id = info.native_chat_id if info is not None else ""
    if hasattr(bot, "get_group_member_list") and str(chat_id).isdigit():
        try:
            for m in await bot.get_group_member_list(group_id=int(chat_id)):
                uid = str(m.get("user_id") or "")
                if not uid:
                    continue
                for name in ((m.get("nickname") or "").strip(), (m.get("card") or "").strip()):
                    if name:
                        results.append(MemberName(name, uid))
        except Exception as e:
            logger.error(f"获取群成员列表失败: {e}")
    return results


async def resolve_at_target(bot: Bot, info: SessionInfo | None, target) -> str | None:
    """把 @ 目标规范成用户 id（与回复/调用共用）

    - 纯数字 → 直接使用（不发请求）
    - 其余按昵称 / 群名片 / 用户 id 精确匹配成员列表（1.6.3 合并 PR #1 后放开群名片）
    解析不出返回 None（调用方决定跳过或提示）。
    """
    text = str(target or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    for member in await list_member_names(bot, info):
        if text == member.name or text == member.user_id:
            return member.user_id
    return None


def _looks_like_ascii_word(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")


def split_text_mentions(text: str, members: list[MemberName]) -> list[tuple[str, str]]:
    """把文本里能精确匹配成员的 `@昵称 / ＠昵称 / @QQ号 / @群名片` 拆成文本段与 at 段

    返回 [("text", 文本) | ("at", 用户id), ...]。只转换能匹配到成员的名字，其余原样保留；
    同名成员（同一名字对应多人）无法判断 @ 的是谁，不自动转换，避免误 @；
    英文/数字结尾的名字还要求后面不是 ASCII 单词字符，避免把 `@Alice2` 误拆成 `@Alice`。
    移植自 main 1.6.3（PR #1，`message_builder._split_text_mentions`）。
    """
    if not text or not members or not any(mark in text for mark in ("@", "＠")):
        return [("text", text)]
    # 名字 → 用户 id；重名且指向不同人时置 None（不转换）
    targets: dict[str, str | None] = {}
    for member in members:
        for name in (member.name, member.user_id):
            if not name:
                continue
            if name in targets and targets[name] != member.user_id:
                targets[name] = None
            else:
                targets[name] = member.user_id
    ordered = sorted(
        ((name, uid) for name, uid in targets.items() if name and uid),
        key=lambda item: len(item[0]),
        reverse=True,  # 长名字优先，避免短名字抢先匹配
    )
    if not ordered:
        return [("text", text)]

    parts: list[tuple[str, str]] = []
    cursor = 0
    search_from = 0
    while True:
        at_positions = [pos for pos in (text.find(mark, search_from) for mark in ("@", "＠")) if pos >= 0]
        if not at_positions:
            break
        start = min(at_positions)
        matched: tuple[str, str] | None = None
        for name, uid in ordered:
            end = start + 1 + len(name)
            if not text.startswith(name, start + 1):
                continue
            if _looks_like_ascii_word(name[-1]) and end < len(text) and _looks_like_ascii_word(text[end]):
                continue
            matched = (name, uid)
            break
        if matched is None:
            search_from = start + 1
            continue
        name, uid = matched
        if start > cursor:
            parts.append(("text", text[cursor:start]))
        parts.append(("at", uid))
        cursor = start + 1 + len(name)
        search_from = cursor
    if cursor < len(text):
        parts.append(("text", text[cursor:]))
    return parts or [("text", text)]


def migrate_legacy_dirs(data_dir: Path) -> int:
    """旧版会话目录（裸 id）→ onebot11_{id}（一次性、幂等）

    旧版本的 memory/{群号} 目录没有适配器前缀，这里在启动时重命名；
    目标已存在时跳过（避免覆盖新版数据）。失败只记日志，不影响启动。
    """
    memory_dir = data_dir / "memory"
    if not memory_dir.is_dir():
        return 0
    moved = 0
    try:
        children = list(memory_dir.iterdir())
    except OSError as e:
        logger.warning(f"[迁移] 读取 {memory_dir} 失败: {e}")
        return 0
    for child in children:
        if not child.is_dir() or not child.name.isdigit():
            continue
        target = memory_dir / disk_key(session_key(LEGACY_SLUG, child.name))
        if target.exists():
            continue
        try:
            child.rename(target)
        except OSError as e:
            logger.warning(f"[迁移] {child.name} → {target.name} 失败: {e}")
            continue
        moved += 1
    if moved:
        logger.success(f"[迁移] 旧会话目录已迁移到 {LEGACY_SLUG}_ 前缀: {moved} 个")
    return moved
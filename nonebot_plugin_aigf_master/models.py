"""统一数据模型定义"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class ChatMessage:
    """群聊消息"""
    time: datetime
    user_name: str
    content: str
    user_id: str = ""
    is_at_only: bool = False


@dataclass
class ReplySegment:
    """回复段落（文本/表情包/@）"""
    type: Literal["text", "meme", "at"]
    content: str = ""
    meme_id: str = ""
    user_name: str = ""


@dataclass
class MemeEntry:
    """表情包条目"""
    id: str
    filename: str
    keywords: list[str] = field(default_factory=list)
    description: str = ""
    usage_count: int = 0
    saved_at: float = 0.0


@dataclass
class PluginMessage:
    """其它插件输出的消息"""
    content: str
    source_plugin: str | None
    group_id: int
    timestamp: datetime
    message_type: Literal["text", "image"]


@dataclass
class PluginCommand:
    """可调用的插件命令"""
    name: str
    aliases: list[str] = field(default_factory=list)
    plugin_name: str = ""
    description: str = ""


@dataclass
class RolePreset:
    """角色预设"""
    name: str
    role: str
    knowledges: list[str] = field(default_factory=list)
    hidden: bool = False


@dataclass
class MemoryOps:
    """记忆操作指令"""
    short_term: dict | None = None
    long_term: dict | None = None
    friends: dict | None = None
    save_meme: list | None = None
    culture: dict | None = None


@dataclass
class ImageInfo:
    """图片描述信息"""
    description: str
    emotion: str
    is_sticker: bool = False

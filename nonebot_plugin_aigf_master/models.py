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
    session_key: str
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
class AnimeHit:
    """角色识别命中的一条角色"""
    name: str
    score: float | None = None   # 本地 WD14 的置信度；AnimeTrace 只给 not_confident 布尔，记为 None
    work: str = ""               # 作品名（仅 AnimeTrace 提供）


@dataclass
class AnimeResult:
    """一张图的角色识别结果"""
    hits: list[AnimeHit] = field(default_factory=list)   # 已按置信度裁定；空 = 成功应答但无可信结果
    source: str = ""                                     # 命中的后端：anime-recognize / animetrace；空 = 未能识别
    rating: dict = field(default_factory=dict)           # WD14 画风分级概率（仅本地后端提供）
    people_count: int | None = None


def anime_hits_to_json(hits: list[AnimeHit] | None) -> list[dict] | None:
    """角色命中 → 可 JSON 化的结构"""
    if hits is None:
        return None
    return [{"name": h.name, "score": h.score, "work": h.work} for h in hits]


def anime_hits_from_json(raw) -> list[AnimeHit] | None:
    """可 JSON 化的结构 → 角色命中（兼容旧缓存的 [[标签, 置信度], ...]）"""
    if raw is None:
        return None
    hits = []
    for item in raw:
        if isinstance(item, dict):
            score = item.get("score")
            hits.append(AnimeHit(
                str(item.get("name", "")),
                float(score) if score is not None else None,
                str(item.get("work") or ""),
            ))
        elif isinstance(item, (list, tuple)) and item:
            hits.append(AnimeHit(str(item[0]), float(item[1]) if len(item) > 1 else None))
    return hits


@dataclass
class ImageInfo:
    """图片描述信息"""
    description: str
    emotion: str
    is_sticker: bool = False
    anime_hits: list[AnimeHit] | None = None  # None=未触发/全部后端失败（静默），[]=已确认未能识别
    anime_rating: dict | None = None          # WD14 画风分级概率
    anime_people_count: int | None = None     # 图中人数（本地=Ngirls/Nboys 取最大；AnimeTrace=检测框数）

"""插件配置定义"""

from typing import Literal

from nonebot import get_driver, get_plugin_config
from pydantic import BaseModel, Field


class PluginConfig(BaseModel):
    # LLM 配置
    aigfm_llm_api_key: str = Field(..., description="LLM API Key")
    aigfm_llm_model: str = Field("gpt-3.5-turbo", description="LLM 模型名称")
    aigfm_llm_base_url: str = Field(..., description="LLM API 地址")
    aigfm_llm_json_mode: bool = Field(True, description="是否强制 LLM 输出 JSON")
    aigfm_llm_tools_json_strict: bool = Field(False, description="工具调用路径首次请求也强制 JSON 输出（部分 OpenAI 兼容服务不支持 tools+json_object 组合，开启前请确认服务商支持）")
    aigfm_llm_temperature: float = Field(0.5, description="LLM 温度参数")

    # VLM 配置
    aigfm_image_mode: Literal["vlm", "llm"] = Field("vlm", description="图片理解模式")
    aigfm_vlm_enabled: bool = Field(True, description="是否启用 VLM")
    aigfm_vlm_model: str = Field("", description="VLM 模型名称")
    aigfm_vlm_base_url: str = Field("", description="VLM API 地址")
    aigfm_vlm_api_key: str = Field("", description="VLM API Key（为空时使用 llm_api_key）")
    # 二次元角色识别（两种后端：本地 WD14 推理服务 aigfm-anime-recognize / AnimeTrace 公共 API，默认关闭）
    aigfm_anime_backend: Literal["off", "anime-recognize", "animetrace", "both"] = Field(
        "off",
        description="角色识别后端：off=关闭；anime-recognize=本地 WD14 服务（需独立部署）；animetrace=AnimeTrace 公共 API（擅长 gal/视觉小说）；both=先本地、置信度不足再用 AnimeTrace",
    )
    aigfm_anime_recognize_enabled: bool = Field(False, description="【已由 aigfm_anime_backend 取代，仅兼容保留】为 true 且 backend=off 时按 anime-recognize 处理")
    aigfm_anime_recognize_url: str = Field("", description="本地角色识别服务地址（如 http://127.0.0.1:8000）")
    aigfm_anime_recognize_token: str = Field("", description="本地角色识别服务 Bearer token（服务端未启用鉴权时留空）")
    aigfm_anime_recognize_min_confidence: float = Field(0.85, description="角色标签展示的最低置信度；both 模式下也用它判断本地是否有结果")
    aigfm_anime_recognize_max_characters: int = Field(3, description="展示的角色标签数量上限")
    aigfm_anime_nsfw_threshold: float = Field(0.5, description="explicit 概率超过该值时在渲染中标注 [NSFW]（仅本地后端提供分级）")
    aigfm_animetrace_url: str = Field("https://api.animetrace.com", description="AnimeTrace API 地址（公共 API，无需鉴权；识别会把图片上传到该服务）")
    aigfm_animetrace_timeout: float = Field(20.0, description="AnimeTrace 请求超时时间（秒）")
    # AI 生图（OpenAI 兼容 images API）
    aigfm_image_gen_enabled: bool = Field(False, description="是否启用 AI 生图")
    aigfm_image_gen_model: str = Field("", description="生图模型名称")
    aigfm_image_gen_base_url: str = Field("", description="生图 API 地址")
    aigfm_image_gen_api_key: str = Field("", description="生图 API Key（为空时使用 llm_api_key）")
    aigfm_image_gen_max_size: str = Field("1024x1024", description="生图最大尺寸限制（宽x高），LLM 可指定比例/尺寸，程序自动缩放到不超过该上限")
    aigfm_image_gen_min_size: str = Field("", description="生图最小尺寸（宽x高），用于满足服务的最低像素要求（如豆包 Seedream 需 ≥ 1920x1920 即 3686400 像素）；为空不限制，非空时按比例放大到不小于该面积（最小优先于最大）")
    aigfm_image_gen_watermark: bool = Field(False, description="生图水印（参考豆包类服务，仅参考图路径传入 watermark 参数）")

    # 会话配置（群/频道与私聊分开；条目可写 `适配器:会话id`，裸 id 一律按 onebot11 识别）
    aigfm_enabled_groups: list[int | str] = Field(
        default_factory=list,
        description="启用的群/频道会话列表；可写 `onebot11:617770183` 形式的会话键，也可只写群号（按 onebot11 识别，兼容旧配置）",
    )
    aigfm_enabled_private: list[int | str] = Field(
        default_factory=list,
        description="启用的私聊会话列表；可写 `适配器:用户id`，也可只写用户 id（按 onebot11 识别）。为空则私聊不响应",
    )
    aigfm_default_preset: str = Field("default", description="默认预设名称")

    # 消息批处理
    aigfm_batch_count: int = Field(10, description="攒满多少条消息后触发 LLM 请求")
    aigfm_batch_timeout: float = Field(30.0, description="距最后一条消息多少秒后触发")
    aigfm_incomplete_timeout: float = Field(40.0, description="消息可能不完整时的等待时间")
    aigfm_merge_window: float = Field(5.0, description="同一用户连续消息合并窗口（秒）")
    aigfm_recent_messages: int = Field(20, description="prompt 中包含的最近历史消息条数")
    aigfm_show_message_time: bool = Field(False, description="是否在 LLM 看到的消息前显示时间（新消息与最近聊天记录，格式 [MM-DD HH:MM]）")

    # 社交能量
    aigfm_energy_baseline: float = Field(0.7, description="社交能量基线（0.0~1.0）")

    # 表情包
    aigfm_meme_enabled: bool = Field(True, description="是否启用表情包功能")
    aigfm_meme_max_count: int = Field(200, description="自动收集表情包最大数量")

    # 搜索
    aigfm_search_enabled: bool = Field(False, description="是否启用联网搜索")
    aigfm_search_api: Literal["tavily", "bocha", "bing", "openwebsearch"] = Field("tavily", description="搜索 API")
    aigfm_search_api_key: str = Field("", description="搜索 API Key（tavily/bocha/bing 必填）")
    aigfm_search_max_results: int = Field(3, description="最大搜索结果数")
    aigfm_openwebsearch_url: str = Field("", description="open-websearch 本地服务地址（如 http://127.0.0.1:3210）")

    # 代理
    aigfm_proxy_enabled: bool = Field(False, description="是否启用代理")
    aigfm_http_proxy: str = Field("", description="HTTP 代理地址")
    aigfm_https_proxy: str = Field("", description="HTTPS 代理地址")

    # 跨插件上下文
    aigfm_capture_plugins: list[str] = Field(default_factory=list, description="插件白名单（捕获+命令扫描+调用核对共用）：非空=只捕获/扫描这些插件且只允许调用它们；空=捕获所有插件输出、但不扫描静态命令、调用不核对")
    aigfm_context_max_messages: int = Field(50, description="每群最大上下文缓冲条数")
    aigfm_context_in_prompt: int = Field(10, description="注入到 prompt 中的其它插件消息条数")
    aigfm_capture_images: bool = Field(True, description="是否捕获并解析其它插件输出的图片")
    aigfm_sticker_cache_max_files: int = Field(300, description="图片缓存(sticker_cache)最大文件数，超限按 mtime 最旧删除（索引常驻后防磁盘无限增长）")
    aigfm_image_cache_max_files: int = Field(500, description="图片描述/角色识别缓存(image_cache 下的 JSON)最大文件数，超限按 mtime 最旧删除（0=不清理）")
    aigfm_raw_cache_max_files: int = Field(200, description="原始图片缓存(raw 目录，按 fileid 命名)最大文件数，超限按 mtime 最旧删除（0=不清理）")

    # 插件调用
    aigfm_invoke_enabled: bool = Field(True, description="是否允许 LLM 调用其它插件")
    aigfm_invoke_timeout: float = Field(30.0, description="插件调用超时时间（秒）")
    aigfm_invoke_dedup_enabled: bool = Field(True, description="拒绝调用最近调用过的相同命令（命令+参数+调用身份完全相同）")
    aigfm_invoke_dedup_window: float = Field(1200.0, description="去重时间窗口（秒，默认 1200=20 分钟；0=不限时间，只看最近 N 条台账）")
    aigfm_invoke_dedup_records: int = Field(15, description="去重比对保留的调用台账条数（默认 15；prompt 里只展示其中最近 10 条）")
    aigfm_invoke_energy_cost: float = Field(0.1, description="每次调用插件消耗的社交能量（0 表示不消耗）")
    aigfm_invoke_energy_min: float = Field(0.4, description="社交能量低于此值时拒绝调用插件（0 表示不限制）")

    # 命令学习
    aigfm_learn_commands: bool = Field(True, description="是否通过群聊学习未注册的命令")
    aigfm_learn_min_confidence: int = Field(3, description="命令学习的最小置信度（观察次数）")

    # 跨 bot（其它 NoneBot 实例，同一 QQ 号下 self_id 相同，只能用 name/token 区分）
    aigfm_peer_bots: list[dict] = Field(
        default_factory=list,
        description="其它 bot 列表，每项含 name(显示名)/port(HTTP端口)/token(共享密钥)",
    )


plugin_config: PluginConfig = get_plugin_config(PluginConfig)
global_config = get_driver().config

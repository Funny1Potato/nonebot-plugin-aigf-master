"""消息处理编排器"""

import asyncio
import base64
import random
from datetime import datetime
from pathlib import Path

import anyio
import httpx
from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .command_learner import CommandLearner
from .config import PluginConfig
from .context_bus import ContextBus
from .image_gen_client import ImageGenClient
from .image_handler import ImageHandler
from .llm_client import LLMClient
from .meme_store import MemeStore
from .memory_store import MemoryStore
from .models import ChatMessage, PluginCommand, ReplySegment
from .peer_client import PeerClient
from .plugin_invoker import PluginInvoker
from .preset_store import PresetStore
from .prompt_builder import build_prompt
from .response_parser import extract_command_learning, extract_memory_ops, extract_reply_segments, parse_llm_response
from .search_client import SearchClient


def _command_head(command: str) -> str:
    """去掉命令前缀并取主命令名（用于调用核对）"""
    command = command.strip()
    try:
        command_start = get_driver().config.command_start
    except Exception:
        command_start = set()
    for prefix in sorted((p for p in command_start if p), key=len, reverse=True):
        if command.startswith(prefix):
            command = command[len(prefix):]
            break
    return command.split(None, 1)[0] if command else command


def _normalize_size(size_desc: str, max_size: str, min_size: str = "") -> str:
    """把 LLM 的尺寸描述（方向词或 WxH）缩放到不超过 max_size 的偶数尺寸

    支持：square/方形/1:1、portrait/竖版/竖屏、landscape/横版/横屏、
    16:9/宽幅/banner、4:3，或直接写 宽x高（如 768x1024）。
    结果按比例缩放到不超过 max_size，并对齐到偶数像素。
    min_size 非空时：若结果面积小于 min_size 面积（宽x高），按比例放大到不小于该面积（最小优先于最大，
    服务于有最低像素要求的生图服务，如豆包 Seedream 需 ≥ 1920x1920 即 3686400 像素）。
    """
    max_w, max_h = 1024, 1024
    try:
        mw, mh = max_size.lower().split("x")
        max_w, max_h = int(mw), int(mh)
    except (ValueError, AttributeError):
        pass
    max_w = max(max_w, 64)
    max_h = max(max_h, 64)

    min_pixels = 0
    if min_size:
        try:
            min_w, min_h = min_size.lower().split("x")
            min_pixels = int(min_w) * int(min_h)
        except (ValueError, AttributeError):
            min_pixels = 0

    ratio = 1.0  # 宽/高
    if size_desc:
        s = size_desc.strip().lower()
        if "x" in s:
            try:
                w, h = s.split("x")
                ratio = int(w) / int(h)
            except (ValueError, ZeroDivisionError):
                ratio = 1.0
        elif s in ("portrait", "竖版", "竖屏", "2:3"):
            ratio = 2 / 3
        elif s in ("landscape", "横版", "横屏", "3:2"):
            ratio = 3 / 2
        elif s in ("16:9", "宽幅", "banner"):
            ratio = 16 / 9
        elif s in ("4:3",):
            ratio = 4 / 3
        # square/方形/1:1 及其它未知描述 → ratio = 1.0

    if ratio >= 1.0:
        w, h = max_w, round(max_w / ratio)
    else:
        w, h = round(max_h * ratio), max_h
    w -= w % 2
    h -= h % 2
    w = max(w, 64)
    h = max(h, 64)
    if min_pixels and w * h < min_pixels:
        scale = (min_pixels / (w * h)) ** 0.5
        w, h = round(w * scale), round(h * scale)
        w -= w % 2
        h -= h % 2
        while w * h < min_pixels:  # 偶数取整后仍可能略低于下限，循环补边
            if w >= h:
                w += 2
            else:
                h += 2
    return f"{w}x{h}"


# 本插件自身的管理命令不允许被 LLM 代为执行：synthetic 事件携带真实触发用户的 user_id，
# 若该用户是 superuser，`/status`、`/reset` 等会被真的执行
SELF_PLUGIN_NAMES = {"nonebot_plugin_aigf_master", "nonebot-plugin-aigf-master"}
# 与 __init__.py 的 on_command 主名 + 别名保持同步（新增/改名管理命令需一并维护）
SELF_COMMANDS = {
    "status", "状态",
    "reset", "重置",
    "set_role", "设置角色",
    "presets", "preset",
    "set_preset", "set_presets",
    "reload_meme", "重载表情包",
}


class MessageProcessor:
    """消息处理编排器，协调所有组件"""

    def __init__(
        self,
        group_id: str,
        llm: LLMClient,
        memory: MemoryStore,
        memes: MemeStore,
        presets: PresetStore,
        context_bus: ContextBus,
        invoker: PluginInvoker,
        learner: CommandLearner,
        search: SearchClient | None,
        config: PluginConfig,
        peer_client: PeerClient | None = None,
        peer_scanned_commands: dict[str, list[dict]] | None = None,
        image_gen: ImageGenClient | None = None,
        image_handler: ImageHandler | None = None,
    ):
        self.group_id = group_id
        self.llm = llm
        self.memory = memory
        self.memes = memes
        self.presets = presets
        self.context_bus = context_bus
        self.invoker = invoker
        self.learner = learner
        self.search = search
        self.config = config
        self.peer_client = peer_client
        self.peer_scanned_commands = peer_scanned_commands if peer_scanned_commands is not None else {}
        self.image_gen = image_gen
        self.image_handler = image_handler
        self._image_gen_tasks: set[asyncio.Task] = set()
        self.bot_name = "小助手"
        self.bot_role = "一个友好的群聊助手"
        self.current_preset = ""
        self.recent_messages: list[ChatMessage] = []
        self.social_energy = 0.75
        self._bot = None
        self._current_user_id: int = 0

    async def load_preset(self, name: str) -> bool:
        preset = self.presets.get(name)
        if not preset:
            return False
        self.bot_name = preset.name
        self.bot_role = preset.role
        self.current_preset = name
        return True

    def status(self) -> str:
        recent = "\n".join(f"{m.user_name}: {m.content}" for m in self.recent_messages[-15:]) or "没有消息"
        return f"名字：{self.bot_name}\n设定：{self.bot_role}\n\n社交能量：{self.social_energy:.2f}\n\n最近消息：\n{recent}"

    async def process(
        self, messages: list[ChatMessage], cached_stickers: list[dict] | None = None,
    ) -> list[ReplySegment] | None:
        logger.info(f"[处理] 群{self.group_id} 开始处理 {len(messages)} 条消息")
        for msg in messages:
            logger.debug(f"[处理] 消息: [{msg.user_name}] {msg.content[:80]}")

        # 更新最近消息
        self.recent_messages.extend(messages)
        if len(self.recent_messages) > 50:
            self.recent_messages = self.recent_messages[-50:]

        # 加载记忆
        short_term = await self.memory.load_short_term()
        long_term = await self.memory.load_long_term()
        friends = await self.memory.load_group_friends()
        culture = await self.memory.load_culture()

        # LLM 模式：读取缓存图片的 base64
        sticker_images: list[str] = []
        if self.config.aigfm_image_mode == "llm" and cached_stickers:
            import base64
            for s in cached_stickers:
                cache_info = self.memes.get_cache_info(int(self.group_id), s["id"])
                if cache_info:
                    try:
                        import anyio
                        async with await anyio.open_file(cache_info["path"], "rb") as f:
                            sticker_images.append(base64.b64encode(await f.read()).decode())
                    except Exception as e:
                        logger.error(f"读取缓存图片失败: {e}")

        # 更新社交能量
        at_boost = 0.0
        for msg in messages:
            if f"@{self.bot_name}" in msg.content or msg.is_at_only:
                at_boost = 0.1
                break

        self.social_energy += (self.config.aigfm_energy_baseline - self.social_energy) * 0.15
        self.social_energy += random.uniform(-0.08, 0.08)
        self.social_energy += at_boost
        self.social_energy = max(0.0, min(1.0, self.social_energy))

        # 匹配文化词汇
        all_text = " ".join(m.content for m in messages)
        matched_culture = MemoryStore.match_culture(culture, all_text)

        # 获取命令列表
        from .plugin_discovery import discover_commands
        if self.config.aigfm_capture_plugins:
            static_commands = [c for c in discover_commands()
                               if c.plugin_name in self.config.aigfm_capture_plugins]
        else:
            static_commands = []          # 白名单空 → 不扫描静态命令
        learned_commands = self.learner.get_all_commands()
        # 合并：重名时用学习命令的丰富描述覆盖静态，保留静态的 plugin_name/aliases
        cmd_map: dict[str, PluginCommand] = {}
        for c in static_commands:
            cmd_map[c.name] = c
        for c in learned_commands:
            if c.name in cmd_map:
                static = cmd_map[c.name]
                cmd_map[c.name] = PluginCommand(
                    name=c.name, aliases=static.aliases, plugin_name=static.plugin_name,
                    description=c.description if c.description else static.description,
                )
            else:
                cmd_map[c.name] = c
        plugin_commands = list(cmd_map.values())
        # peer 命令：学习命令（含具体用法，优先）+ 扫描命令（补充）
        peer_map: dict[str, dict] = {}
        for cmd in self.learner.get_peer_commands():
            peer_map[cmd["name"]] = cmd
        for bot_name, cmds in self.peer_scanned_commands.items():
            for c in cmds:
                name = c.get("name", "")
                if not name or name in peer_map:
                    continue
                peer_map[name] = {
                    "name": name,
                    "bot": bot_name,
                    "description": c.get("description", ""),
                }
        peer_commands = list(peer_map.values())
        logger.debug(f"[命令] 静态: {len(static_commands)}, 学习: {len(learned_commands)}, peer: {len(peer_commands)}")

        # 构建 prompt
        preset = self.presets.get(self.current_preset)
        bus_messages = self.context_bus.get_recent(int(self.group_id), self.config.aigfm_context_in_prompt)
        meme_prompt_list = self.memes.prompt_list()
        prompt = build_prompt(
            bot_name=self.bot_name, bot_role=self.bot_role,
            social_energy=self.social_energy,
            short_term=short_term, long_term=long_term, friends=friends,
            recent_messages=self.recent_messages, new_messages=messages,
            meme_prompt_list=meme_prompt_list,
            cached_stickers=cached_stickers or [],
            matched_culture=matched_culture,
            culture=culture,
            plugin_commands=plugin_commands, peer_commands=peer_commands,
            context_bus_messages=bus_messages,
            preset=preset, image_mode=self.config.aigfm_image_mode, config=self.config,
        )

        # 调用 LLM
        logger.info(f"[处理] 群{self.group_id} 调用 LLM, sticker_images: {len(sticker_images)} 张")
        recent_5 = "\n".join(f"  [{m.user_name}] {m.content[:80]}" for m in self.recent_messages[-5:])
        logger.debug(f"[处理] 最近聊天记录(最新5条):\n{recent_5}")

        used_tool = False
        if (self.config.aigfm_search_enabled and self.search):
            response_str, used_tool = await self._call_llm_with_tools(prompt, sticker_images, plugin_commands, peer_commands)
        else:
            response_str = await self.llm.chat(
                prompt, self.config.aigfm_llm_model,
                json_mode=self.config.aigfm_llm_json_mode,
                images=sticker_images if sticker_images else None,
            )

        if not response_str:
            return None

        logger.info(f"[LLM] 响应: {response_str[:200]}")

        # 解析响应
        result = parse_llm_response(response_str)
        if not result:
            if used_tool:
                logger.warning("[处理] JSON 解析失败，去掉 tools 重试一次")
                retry_prompt = prompt + "\n## 注意\n上一次输出无法解析为 JSON。请仅根据对话上下文回复，**只输出 JSON，不要附带任何多余文字**。\n\n"
                response_str = await self.llm.chat(
                    retry_prompt, self.config.aigfm_llm_model,
                    json_mode=self.config.aigfm_llm_json_mode,
                    images=sticker_images if sticker_images else None,
                )
                if not response_str:
                    return None
                result = parse_llm_response(response_str)
                if not result:
                    logger.error(f"[处理] 重试后仍解析失败: {response_str[:200]}")
                    return None
            else:
                logger.error(f"[处理] JSON 解析失败: {response_str[:200]}")
                return None

        # 执行记忆操作
        memory_ops = extract_memory_ops(result)
        logger.debug(f"[记忆] 操作: short_term={bool(memory_ops.short_term)}, long_term={bool(memory_ops.long_term)}, friends={bool(memory_ops.friends)}, save_meme={bool(memory_ops.save_meme)}, culture={bool(memory_ops.culture)}")
        try:
            await self.memory.apply_ops(memory_ops)
        except Exception as e:
            logger.error(f"执行记忆操作失败: {e}")

        # 命令学习
        if self.config.aigfm_learn_commands:
            cmd_ops = extract_command_learning(result)
            learn_ops = cmd_ops.get("learn", [])
            edit_ops = cmd_ops.get("edit", [])
            delete_ops = cmd_ops.get("delete", [])
            if learn_ops or edit_ops or delete_ops:
                logger.debug(f"[命令学习] 操作: learn={len(learn_ops)}, edit={len(edit_ops)}, delete={len(delete_ops)}")
            for cmd in learn_ops:
                self.learner.learn_command(
                    cmd["name"], cmd["parameters"], cmd["usage"], cmd["hook_type"],
                    cmd["source"], cmd.get("examples"),
                )
            for cmd in edit_ops:
                self.learner.edit_command(cmd["name"], cmd.get("parameters"), cmd.get("usage"), cmd.get("examples"))
            for name in delete_ops:
                self.learner.delete_command(name)

        # 表情包收藏
        if memory_ops.save_meme and self.config.aigfm_meme_enabled:
            await self._save_memes(memory_ops.save_meme, cached_stickers or [])

        # 提取回复
        reply_segments = extract_reply_segments(result)
        if not reply_segments:
            return None

        # 更新最近消息
        self._record_bot_reply(reply_segments)

        # 消耗社交能量
        text_length = sum(len(s.content) for s in reply_segments if s.type == "text")
        energy_cost = 0.03 + min(0.12, text_length * 0.002)
        self.social_energy = max(0.0, self.social_energy - energy_cost)
        logger.info(f"[能量] 消耗: {energy_cost:.3f}, 剩余: {self.social_energy:.3f}")

        return reply_segments

    async def _call_llm_with_tools(self, prompt: str, sticker_images: list[str],
                                   plugin_commands: list | None = None,
                                   peer_commands: list | None = None) -> tuple[str | None, bool]:
        # 命令清单为空时不注入命令调用工具：模型看不到工具就不会填「无/none」占位值，
        # 命令学习走 JSON 输出字段，不受影响；学到第一条命令后清单非空，工具自动出现
        plugin_commands = plugin_commands or []
        peer_commands = peer_commands or []
        tools = []
        if self.config.aigfm_search_enabled:
            tools.append({
                "type": "function", "function": {
                    "name": "search_internet",
                    "description": "搜索互联网获取最新信息",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                },
            })
        if self.config.aigfm_invoke_enabled and plugin_commands:
            tools.append({
                "type": "function", "function": {
                    "name": "invoke_plugin",
                    "description": "调用本机群内的其它功能插件（仅限「可用的群功能」清单中列出的本机命令）",
                    "parameters": {"type": "object", "properties": {
                        "command": {"type": "string", "description": "命令（不带前缀），必须逐字来自 prompt 中「可用的群功能」清单（本机插件提供的命令）。「其它 bot 的命令」清单里的命令不属于本工具，要用 invoke_peer_plugin。**用户没有明确要求时绝对不要调用本工具，话题相关不等于要求执行**；只有用户明确要求执行某个功能时才调用；没有要执行的命令就不要使用本工具，禁止填写 无/没有/none/null 之类占位值，也不要自行编造命令名"},
                        "user_id": {"type": "integer", "description": "命令归属的用户 QQ 号（可选，可从群友信息中选择任意群友，不同 QQ 号调用可能返回不同结果，默认当前消息发送者）"},
                        "at_user_id": {"type": "integer", "description": "命令要 @ 的群友 QQ 号（可选，如\"决斗 @张三\"这类命令的目标；从「已知群友昵称」的名字(QQ:号) 中取，不需要 @ 时省略）"}
                    }, "required": ["command"]},
                },
            })
        if self.config.aigfm_invoke_enabled and self.peer_client and peer_commands:
            peer_names = "、".join(self.peer_client.names())
            tools.append({
                "type": "function", "function": {
                    "name": "invoke_peer_plugin",
                    "description": f"调用其它 bot（{peer_names}）上的功能插件（仅限「其它 bot 的命令」清单中列出的命令）",
                    "parameters": {"type": "object", "properties": {
                        "bot": {"type": "string", "description": f"目标 bot 名，可选: {peer_names}"},
                        "command": {"type": "string", "description": "命令（不带前缀），必须逐字来自 prompt 中「其它 bot 的命令」清单（其它 bot 提供的命令）。「可用的群功能」清单里的本机命令不属于本工具，要用 invoke_plugin。**用户没有明确要求时绝对不要调用本工具，话题相关不等于要求执行**；只有用户明确要求执行某个功能时才调用；没有要执行的命令就不要使用本工具，禁止填写 无/没有/none/null 之类占位值，也不要自行编造命令名"},
                        "user_id": {"type": "integer", "description": "命令归属的用户 QQ 号（可选，可从群友信息中选择任意群友，不同 QQ 号调用可能返回不同结果，默认当前消息发送者）"},
                        "at_user_id": {"type": "integer", "description": "命令要 @ 的群友 QQ 号（可选，如\"决斗 @张三\"这类命令的目标；从「已知群友昵称」的名字(QQ:号) 中取，不需要 @ 时省略）"}
                    }, "required": ["bot", "command"]},
                },
            })
        if self.config.aigfm_image_gen_enabled and self.image_gen:
            tools.append({
                "type": "function", "function": {
                    "name": "generate_image",
                    "description": "使用 AI 生图模型根据描述生成图片并自动发到群里（仅当用户明确要求生成/画图时才调用）",
                    "parameters": {"type": "object", "properties": {
                        "prompt": {"type": "string", "description": "详细的图片描述（画面内容、风格、色调、比例等），越具体生成效果越好"},
                        "size": {"type": "string", "description": "可选尺寸/比例：square/方形、portrait/竖版、landscape/横版、16:9/宽幅，或直接写宽x高（如 768x1024）；默认方形，程序会自动限制在配置的最大尺寸内"},
                        "reference_cache_id": {"type": "string", "description": "可选参考图 id：聊天记录中出现的图片 id（如 [发送了一张图片, id: xxx] 或 [回复 xxx 的消息: \"[发送了一张图片, id: xxx]...\"] 中的 xxx）；用户要求\"参考这张图/照着改/换成某种风格\"时填，不要编造不存在的 id，不需要时省略"}
                    }, "required": ["prompt"]},
                },
            })

        async def handler(name: str, args: dict) -> str:
            if name == "search_internet" and self.search:
                results = await self.search.search(args.get("query", ""), self.config.aigfm_search_max_results)
                return self.search.format_results(results)
            elif name == "invoke_plugin":
                command = args.get("command", "")
                uid = args.get("user_id", self._current_user_id)
                from .plugin_discovery import build_command_plugin_map
                main = _command_head(command)
                plugin = build_command_plugin_map().get(main) or self.learner.find_command_plugin(main)
                # 先拒本插件自身的管理命令（不投递、不写自述，避免留下"已执行"的假记录）
                if main in SELF_COMMANDS or plugin in SELF_PLUGIN_NAMES:
                    logger.info(f"[调用] 拒绝: command={command}（本插件管理命令，不可代为执行）")
                    return "这是本插件自己的管理命令，不能代为执行；不要再调用它"
                # 白名单核对：非空时，命令归属插件必须在白名单内
                if self.config.aigfm_capture_plugins:
                    if plugin not in self.config.aigfm_capture_plugins:
                        logger.info(f"[调用] 拒绝: command={command}（插件 {plugin} 不在白名单）")
                        return "该插件不在允许调用的白名单内，已拒绝调用"
                logger.info(f"[调用] invoke_plugin: command={command}, user_id={uid}")
                at_qq = args.get("at_user_id", 0) or 0
                outcome = await self.invoker.invoke(
                    self._bot, int(self.group_id), command, self.config.aigfm_invoke_timeout,
                    user_id=uid, at_qq=at_qq,
                )
                # 记录 bot 自述，让下一次批处理时 LLM 知道这条命令是自己发起的（含选用身份）
                # 投递成功/超时/出错都要记，否则 LLM 会重复调用同一条命令
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(),
                    user_name=self.bot_name,
                    content=f"已调用命令「{command}」（user_id={uid}）",
                ))
                if outcome == "timeout":
                    logger.warning(f"[调用] invoke_plugin 超时: {command}")
                    return (f"命令已投递，但插件未在 {int(self.config.aigfm_invoke_timeout)} 秒内完成执行，"
                            "响应稍后仍可能作为新消息出现，先不要断言失败")
                if outcome == "error":
                    logger.error(f"[调用] invoke_plugin 投递失败: {command}")
                    return "命令投递失败（详见日志），插件不会响应"
                logger.success(f"[调用] invoke_plugin 已投递: {command}")
                return ("命令已投递。只有后续聊天记录里真的出现 [插件名]/[bot名] 的响应才算执行成功；"
                        "一直没有响应说明该命令不存在或无人处理，不要当作已完成")
            elif name == "invoke_peer_plugin":
                peer_name = args.get("bot", "")
                command = args.get("command", "")
                uid = args.get("user_id", self._current_user_id)
                logger.info(f"[调用] invoke_peer_plugin: bot={peer_name}, command={command}, user_id={uid}")
                at_qq = args.get("at_user_id", 0) or 0
                result = await self.peer_client.invoke(
                    peer_name, command, int(self.group_id), uid,
                    at_qq=at_qq, timeout=self.config.aigfm_invoke_timeout,
                )
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(),
                    user_name=self.bot_name,
                    content=f"已调用命令「{command}」（{peer_name}，user_id={uid}）",
                ))
                return result
            elif name == "generate_image":
                prompt = args.get("prompt", "").strip()
                if not prompt:
                    return "缺少图片描述，请提供要生成的画面内容"
                size_desc = args.get("size", "").strip()
                reference_id = args.get("reference_cache_id", "").strip()
                uid = args.get("user_id", self._current_user_id)
                logger.info(f"[生图] 开始: prompt={prompt[:60]}, size={size_desc or '默认方形'}, reference={reference_id or '无'}, user_id={uid}")
                # 防重复：立即写自述，后台完成后另写一条结果自述
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(), user_name=self.bot_name,
                    content=f"已开始生成图片「{prompt}」",
                ))
                task = asyncio.create_task(self._generate_image_bg(prompt, size_desc, uid, reference_id))
                self._image_gen_tasks.add(task)
                task.add_done_callback(self._image_gen_tasks.discard)
                return "图片生成任务已启动，完成后会自动发到群里；生成需要一点时间，期间请不要重复提交相同的生图请求"
            return f"未知工具: {name}"

        return await self.llm.chat_with_tools(
            prompt, self.config.aigfm_llm_model, tools, handler,
            json_mode=self.config.aigfm_llm_json_mode,
            first_call_json=self.config.aigfm_llm_tools_json_strict,
        )

    async def _generate_image_bg(self, prompt: str, size_desc: str, uid: int, reference_id: str = ""):
        """后台生图任务：生成 → 发送到群 → VLM 描述 → 写自述（LLM 下一次感知）"""
        try:
            if not (self._bot and self.image_gen):
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(), user_name=self.bot_name,
                    content="图片生成失败：生图功能未就绪",
                ))
                return
            size = _normalize_size(size_desc, self.config.aigfm_image_gen_max_size,
                               self.config.aigfm_image_gen_min_size)
            # 参考图：先查本群缓存索引，未命中则按 id 从磁盘兜底（clear_cache 只清索引不清文件）
            reference_images: list[str] | None = None
            reference_note = ""
            if reference_id:
                ref_path = None
                info = self.memes.get_cache_info(int(self.group_id), reference_id)
                if info and info.get("path") and Path(info["path"]).is_file():
                    ref_path = info["path"]
                else:
                    disk_path = self.memes.get_cache_file(reference_id)
                    if disk_path:
                        ref_path = disk_path
                if ref_path:
                    async with await anyio.open_file(ref_path, "rb") as f:
                        ref_bytes = await f.read()
                    ext = Path(ref_path).suffix.lstrip(".").lower() or "png"
                    reference_images = [f"data:image/{ext};base64,{base64.b64encode(ref_bytes).decode()}"]
                else:
                    logger.warning(f"[生图] 参考图不可用: {reference_id}，改为普通生成")
                    reference_note = "（参考图不可用，已改为普通生成）"
            result = await self.image_gen.generate(
                prompt, size,
                reference_images=reference_images,
                watermark=self.config.aigfm_image_gen_watermark,
            )
            if not result:
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(), user_name=self.bot_name,
                    content=f"图片生成失败：服务未返回图片（「{prompt}」）",
                ))
                return
            # 取图片数据（仅返回 url 时下载）
            b64 = result.get("b64")
            if not b64 and result.get("url"):
                async with httpx.AsyncClient() as client:
                    resp = await client.get(result["url"], timeout=30)
                    resp.raise_for_status()
                    b64 = base64.b64encode(resp.content).decode()
            if not b64:
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(), user_name=self.bot_name,
                    content="图片生成失败：无法获取图片数据",
                ))
                return
            await self._bot.send_msg(
                message_type="group", group_id=int(self.group_id),
                message=Message(MessageSegment.image(f"base64://{b64}")),
            )
            # VLM 描述（本插件自己发的图不会被钩子捕获，需主动描述让 LLM 感知结果）
            desc_text = ""
            if self.image_handler:
                try:
                    info = await self.image_handler.describe(b64, is_sticker=False)
                    if info and info.description:
                        desc_text = f"，内容描述: {info.description}"
                except Exception:
                    desc_text = ""
            logger.success(f"[生图] 已生成并发送: {prompt[:60]} ({size})")
            self.recent_messages.append(ChatMessage(
                time=datetime.now(), user_name=self.bot_name,
                content=f"图片已生成：「{prompt}」（{size}）{desc_text}{reference_note}",
            ))
        except Exception as e:
            logger.error(f"[生图] 失败: {e}")
            self.recent_messages.append(ChatMessage(
                time=datetime.now(), user_name=self.bot_name,
                content=f"图片生成失败：{e}",
            ))

    async def _save_memes(self, save_meme: list, cached_stickers: list[dict]):
        current_ids = {s["id"] for s in cached_stickers}
        for item in save_meme if isinstance(save_meme, list) else [save_meme]:
            cache_id = item.get("id", "")
            description = item.get("description", "")
            keywords = item.get("keywords", ["表情包"])
            if not isinstance(keywords, list):
                keywords = ["表情包"]
            if cache_id and description and cache_id in current_ids:
                try:
                    await self.memes.save_from_cache(int(self.group_id), cache_id, description, keywords)
                except Exception as e:
                    logger.error(f"表情包保存失败: {e}")

    def _record_bot_reply(self, segments: list[ReplySegment]):
        for seg in segments:
            if seg.type == "text":
                self.recent_messages.append(ChatMessage(time=datetime.now(), user_name=self.bot_name, content=seg.content))
            elif seg.type == "meme":
                meme = self.memes.all_memes.get(seg.meme_id)
                desc = meme.description if meme else seg.meme_id
                self.recent_messages.append(ChatMessage(time=datetime.now(), user_name=self.bot_name, content=f"[表情包] {desc}"))
            elif seg.type == "at":
                self.recent_messages.append(ChatMessage(time=datetime.now(), user_name=self.bot_name, content=f"@{seg.user_name}"))

"""消息处理编排器"""

import random
from datetime import datetime

from nonebot import logger

from .config import PluginConfig
from .context_bus import ContextBus
from .command_learner import CommandLearner
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
                cache_info = self.memes.get_cache_info(s["id"])
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
        static_commands = discover_commands()
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
            search_results=None, matched_culture=matched_culture,
            plugin_commands=plugin_commands, peer_commands=peer_commands,
            context_bus_messages=bus_messages,
            preset=preset, image_mode=self.config.aigfm_image_mode, config=self.config,
        )

        # 调用 LLM
        logger.info(f"[处理] 群{self.group_id} 调用 LLM, sticker_images: {len(sticker_images)} 张")
        recent_5 = "\n".join(f"  [{m.user_name}] {m.content[:80]}" for m in self.recent_messages[-5:])
        logger.debug(f"[处理] 最近聊天记录(最新5条):\n{recent_5}")

        used_search = False
        if (self.config.aigfm_search_enabled and self.search):
            response_str, used_search = await self._call_llm_with_tools(prompt, sticker_images)
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
            if search_results or used_search:
                logger.warning("[处理] JSON 解析失败，去掉搜索结果重试")
                prompt_no_search = build_prompt(
                    bot_name=self.bot_name, bot_role=self.bot_role,
                    social_energy=self.social_energy,
                    short_term=short_term, long_term=long_term, friends=friends,
                    recent_messages=self.recent_messages, new_messages=messages,
                    meme_prompt_list=meme_prompt_list,
                    cached_stickers=cached_stickers or [],
                    search_results=None, matched_culture=matched_culture,
                    plugin_commands=plugin_commands, peer_commands=peer_commands,
                    context_bus_messages=bus_messages,
                    preset=preset, image_mode=self.config.aigfm_image_mode, config=self.config,
                )
                prompt_no_search += "\n## 注意\n搜索失败，请仅根据对话上下文回复。**必须输出 JSON 格式**。\n\n"
                response_str = await self.llm.chat(
                    prompt_no_search, self.config.aigfm_llm_model,
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

    async def _call_llm_with_tools(self, prompt: str, sticker_images: list[str]) -> tuple[str | None, bool]:
        tools = []
        tool_defs = {}
        if self.config.aigfm_search_enabled:
            tools.append({
                "type": "function", "function": {
                    "name": "search_internet",
                    "description": "搜索互联网获取最新信息",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                },
            })
            tool_defs["search_internet"] = "search"
        if self.config.aigfm_invoke_enabled:
            tools.append({
                "type": "function", "function": {
                    "name": "invoke_plugin",
                    "description": "调用本机群内的其它功能插件",
                    "parameters": {"type": "object", "properties": {
                        "command": {"type": "string", "description": "命令（不带前缀），如 weather 北京"},
                        "user_id": {"type": "integer", "description": "命令归属的用户 QQ 号（可选，可从群友信息中选择任意群友，不同 QQ 号调用可能返回不同结果，默认当前消息发送者）"}
                    }, "required": ["command"]},
                },
            })
        if self.config.aigfm_invoke_enabled and self.peer_client:
            peer_names = "、".join(self.peer_client.names())
            tools.append({
                "type": "function", "function": {
                    "name": "invoke_peer_plugin",
                    "description": f"调用其它 bot（{peer_names}）上的功能插件",
                    "parameters": {"type": "object", "properties": {
                        "bot": {"type": "string", "description": f"目标 bot 名，可选: {peer_names}"},
                        "command": {"type": "string", "description": "命令（不带前缀），如 weather 北京"},
                        "user_id": {"type": "integer", "description": "命令归属的用户 QQ 号（可选，可从群友信息中选择任意群友，不同 QQ 号调用可能返回不同结果，默认当前消息发送者）"}
                    }, "required": ["bot", "command"]},
                },
            })
            tool_defs["invoke_peer_plugin"] = "invoke"

        async def handler(name: str, args: dict) -> str:
            if name == "search_internet" and self.search:
                results = await self.search.search(args.get("query", ""), self.config.aigfm_search_max_results)
                return self.search.format_results(results)
            elif name == "invoke_plugin":
                command = args.get("command", "")
                uid = args.get("user_id", self._current_user_id)
                logger.info(f"[调用] invoke_plugin: command={command}, user_id={uid}")
                results = await self.invoker.invoke(
                    self._bot, int(self.group_id), command, self.config.aigfm_invoke_timeout,
                    user_id=uid,
                )
                if not results:
                    return "插件无响应"
                logger.success(f"[调用] invoke_plugin 成功: {command}")
                # 记录 bot 自述，让下一次批处理时 LLM 知道是自己调用了插件（含选用身份）
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(),
                    user_name=self.bot_name,
                    content=f"已调用命令「{command}」（user_id={uid}）",
                ))
                return "插件已执行，响应将作为新消息出现在聊天记录中"
            elif name == "invoke_peer_plugin":
                peer_name = args.get("bot", "")
                command = args.get("command", "")
                uid = args.get("user_id", self._current_user_id)
                logger.info(f"[调用] invoke_peer_plugin: bot={peer_name}, command={command}, user_id={uid}")
                result = await self.peer_client.invoke(
                    peer_name, command, int(self.group_id), uid,
                    timeout=self.config.aigfm_invoke_timeout,
                )
                self.recent_messages.append(ChatMessage(
                    time=datetime.now(),
                    user_name=self.bot_name,
                    content=f"已调用命令「{command}」（{peer_name}，user_id={uid}）",
                ))
                return result
            return f"未知工具: {name}"

        return await self.llm.chat_with_tools(prompt, self.config.aigfm_llm_model, tools, handler)

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
                    await self.memes.save_from_cache(cache_id, description, keywords)
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

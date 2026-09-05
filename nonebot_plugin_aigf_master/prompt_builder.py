"""Prompt 构建（纯函数）"""

import json
from datetime import datetime

from .config import PluginConfig
from .models import ChatMessage, PluginCommand, PluginMessage, RolePreset


def _merge_consecutive(messages: list[ChatMessage], window: float) -> list[ChatMessage]:
    """合并同一用户在时间窗口内的连续消息"""
    if not messages:
        return []
    merged: list[ChatMessage] = []
    for msg in messages:
        if (merged and msg.user_id and
                msg.user_id == merged[-1].user_id and
                (msg.time - merged[-1].time).total_seconds() <= window):
            merged[-1] = ChatMessage(
                time=merged[-1].time, user_name=merged[-1].user_name,
                content=merged[-1].content + " " + msg.content, user_id=merged[-1].user_id,
            )
        else:
            merged.append(msg)
    return merged


def _energy_description(energy: float) -> str:
    if energy >= 0.8:
        return "精力充沛，看到什么都想插嘴"
    elif energy >= 0.6:
        return "状态不错，有兴趣的话题会主动参与"
    elif energy >= 0.4:
        return "一般般，有人找就回，不太主动"
    elif energy >= 0.2:
        return "有点懒，倾向于潜水"
    else:
        return "完全不想说话"


def build_prompt(
    bot_name: str,
    bot_role: str,
    social_energy: float,
    short_term: list[str],
    long_term: list[str],
    friends: dict[str, dict],
    recent_messages: list[ChatMessage],
    new_messages: list[ChatMessage],
    meme_prompt_list: str,
    cached_stickers: list[dict],
    matched_culture: list[dict],
    plugin_commands: list[PluginCommand],
    peer_commands: list[dict],
    context_bus_messages: list[PluginMessage],
    preset: RolePreset | None,
    image_mode: str,
    config: PluginConfig,
) -> str:
    """构建完整的 LLM prompt"""

    window = config.aigfm_merge_window
    merged_chunk = _merge_consecutive(new_messages, window)

    # 预设知识
    preset_knowledge = ""
    if preset and preset.knowledges:
        preset_knowledge = "\n".join(f"- {k}" for k in preset.knowledges)

    # 记忆（JSON 格式）
    short_term_str = json.dumps(short_term, ensure_ascii=False, indent=2) if short_term else "[]"
    long_term_str = json.dumps(long_term, ensure_ascii=False, indent=2) if long_term else "[]"

    # 群友信息
    friends_dict = {}
    for uid, data in friends.items():
        friends_dict[uid] = {
            "nickname": data.get("nickname", ""),
            "aliases": data.get("aliases", []),
            "past_nicknames": data.get("past_nicknames", []),
            "info": data.get("info", []),
        }
    active_users = _get_active_user_ids(new_messages)
    for uid in active_users:
        if uid not in friends_dict:
            friends_dict[uid] = {"nickname": "", "aliases": [], "past_nicknames": [], "info": []}
    friends_str = json.dumps(friends_dict, ensure_ascii=False, indent=2) if friends_dict else "{}"

    # 最近消息（合并 ContextBus 消息）；本批消息只在「新消息」段呈现，不重复计入历史
    batch_ids = {id(m) for m in new_messages}
    history = [m for m in recent_messages if id(m) not in batch_ids]
    recent = history[-config.aigfm_recent_messages:]
    recent = _merge_consecutive(recent, window)
    recent_str = _build_recent_str(recent, context_bus_messages, bot_name, window, new_messages)

    # 新消息
    new_msgs_str = "\n".join(f"{m.user_name}: '{m.content}'" for m in merged_chunk)

    # 表情包列表
    meme_section = ""
    if config.aigfm_meme_enabled and meme_prompt_list:
        meme_section = f"\n\n## 可用的表情包（用于发送）\n{meme_prompt_list}\n"

    # 缓存中的表情包
    sticker_section = ""
    if cached_stickers and config.aigfm_meme_enabled:
        sticker_ids = [s['id'] for s in cached_stickers]
        if image_mode == "llm":
            sticker_section = """
## 当前消息中出现的图片（可收藏）
以下图片已通过视觉输入提供。请结合上下文判断是否值得收藏。
- 标记为"可能是表情包"的图片已附带情感和内容描述，是较可能的收藏候选
- 仅标记为"图片"的通常为普通图片，一般不需要收藏
请结合上下文进一步判断：
- 如果是表情包（能表达一定的情感，适合在群聊中反复使用），且你的表情包库内没有类似的图片，且你觉得值得保存的，可以收藏。
- 如果只是群友分享的照片、截图、梗图、普通图片，或者表情包库内已有类似图片，则不要收藏。
- 若群友连续发了多张图片，通常是在分享普通图片
- 若群友只发了一张图片，而前后均没有与这张图片相关的内容，或是在图片前后仅有对该图片的介绍或评论，通常是在分享普通图片
- 若群友发的图片内容与之前的内容有关联，或这张图片是在其它群友发言之后发出的，且**包含较为明显的情感**（开心、愤怒、疑惑等），则可能是表情包
如果值得收藏，在 memory.save_meme 中填入 id、简短描述和关键词。
可用 id：""" + ", ".join(sticker_ids)
        else:
            sticker_section = """
## 当前消息中出现的图片（可收藏）
消息中已标注初步判断结果。请结合上下文进一步判断是否值得收藏。
- 标记为"可能是表情包"的图片已附带情感和内容描述，是较可能的收藏候选
- 仅标记为"图片"的通常为普通图片，一般不需要收藏
请结合上下文进一步判断：
- 如果是表情包（能表达一定的情感，适合在群聊中反复使用），且你的表情包库内没有类似的图片，且你觉得值得保存的，可以收藏。
- 如果只是群友分享的照片、截图、梗图、普通图片，或者表情包库内已有类似图片，则不要收藏。
- 若群友连续发了多张图片，通常是在分享普通图片
- 若群友只发了一张图片，而前后均没有与这张图片相关的内容，或是在图片前后仅有对该图片的介绍或评论，通常是在分享普通图片
- 若群友发的图片内容与之前的内容有关联，或这张图片是在其它群友发言之后发出的，且**包含较为明显的情感**（开心、愤怒、疑惑等），则可能是表情包
如果值得收藏，在 memory.save_meme 中填入 id 和你写的简短描述。
可用 id：""" + ", ".join(sticker_ids)

    # 群友列表
    user_list_str = ", ".join(f"{data.get('nickname', uid)}(QQ:{uid})" for uid, data in friends.items()) if friends else "无"

    # 可用插件列表
    plugin_section = ""
    if plugin_commands and config.aigfm_invoke_enabled:
        cmd_list = "\n".join(f"- {cmd.name} - {cmd.description}" for cmd in plugin_commands)
        plugin_section = f"""
## 可用的群功能
当用户要求使用以下**本机**功能，且聊天记录中**还没有**该命令的调用记录时，调用 invoke_plugin 工具来执行命令，不要自己编造回复：
{cmd_list}

调用示例：用户说"帮我查一下今日小猪"，且你此前未调用过，应调用 invoke_plugin(command="今日小猪")

## 调用身份
调用 invoke_plugin 或 invoke_peer_plugin 时，可通过 user_id 参数指定以哪位群友的身份调用（从上方"相关群友信息/已知群友昵称"中选择 QQ 号）。
**用不同的 QQ 号调用可能得到不同的结果**（如按用户查询的功能，"今日小猪"会查不同用户的猪）。
若用户未指定，默认以当前消息发送者的身份调用。

## 插件响应
调用 invoke_plugin / invoke_peer_plugin 后，插件的响应（文本或图片描述）会以 `[插件名] 内容` 或 `[bot名] 内容` 的形式作为**新消息**出现在后续聊天记录中。
- 调用后，本次回复必须为空（`"reply": []`），**不要说话**，等插件响应出现
- 当聊天记录中出现 `[插件名] 内容` 或 `[bot名] 内容` 后，再根据插件响应决定是否回复、回复什么，**不要编造**插件的内容
- **不要重复调用**：如果聊天记录中已经出现 `[你的名字] 已调用命令「xxx」`，说明该命令已调用过，**绝不要再调用**，直接根据已有内容判断是否回复
- 工具返回"插件未在 N 秒内完成执行"只是**投递超时**，插件稍后仍可能回复 → 等下一批看结果，不要立刻断定失败
- 如果插件响应一直未出现，再视情况告知用户插件未响应

## 重要：已知命令的处理
以下命令已由其他插件注册。当用户发送这些命令时：
- 如果聊天记录中有插件/bot 对该消息做出了响应，你应该**不要回复**，除非用户明确@你或要求你做其他事情
- 如果没有插件/bot 对命令做出响应，你可以视情况回复（如告知命令无响应、尝试帮助等）

## 命令学习与管理
通过观察聊天记录，你可以学习、编辑和删除命令：

1. **学习新命令**：如果聊天记录中出现了你未见过的命令模式（用户发送消息后，有插件/bot 做出了响应），请添加 command_learning 字段：
   "command_learning": [{{"name": "命令名", "parameters": "可选参数说明(无则空字符串)", "usage": "完整使用方法，如 今日小猪 [猪类型]", "hook_type": "current或peer", "source": "current填插件名，peer填bot名", "examples": ["用户发送的原文"]}}]

2. **编辑命令**：如果你发现已有命令的用法或参数不准确，请添加 command_edit 字段：
   "command_edit": [{{"name": "命令名", "parameters": "更新后的参数说明", "usage": "更新后的用法", "examples": ["更新后的示例"]}}]

3. **删除命令**：如果某个命令已不再可用或完全错误，请添加 command_delete 字段：
   "command_delete": ["命令名"]
   注意：删除操作需要多次确认才会生效，所以如果确定命令有问题，请每次都说。

4. **不要学习你无法代为执行的命令**：下面这类命令即使你调用也只会得到"插件无响应"，看到"用户发送 → 插件响应"时**不要学习**；如果已经学习过，用 command_delete 删除：
   - 需要 @你 或呼叫你的昵称才触发的命令
   - 需要管理员/群主/超级用户权限的命令
   - 只在私聊里用的命令
   - 必须先回复/引用某条消息才能执行的命令
   - 依赖群名片、头衔、等级等资料字段判断身份的命令
   注意：不学习 ≠ 可以抢答。它们仍然是命令，只要聊天记录里有插件/bot 响应，你依然不要重复回复。

如果没有需要学习/编辑/删除的命令，不需要添加这些字段。
{cmd_list}
"""

    # 其它 bot 的命令
    if peer_commands:
        peer_cmd_list = "\n".join(f"- {cmd['name']}（来自 {cmd['bot']}）- {cmd.get('description', '')}" for cmd in peer_commands)
        plugin_section += f"""
## 其它 bot 的命令
以下命令由其它 bot 提供。当用户要求使用这些功能时，应调用 **invoke_peer_plugin** 工具，并在 bot 参数指定目标 bot 名：
例如调用 botB 的"今日小猪"：invoke_peer_plugin(bot="botB", command="今日小猪")
**优先使用带有"用法/参数/示例"说明的命令**（这些是从聊天记录中学习到的，调用方式明确）；仅有名和简略描述的命令具体用法不明确，请谨慎调用，必要时先向用户确认。
当用户发送这些命令时：
- 如果有插件/bot 对消息做出了响应，你应该**不要回复**
- 如果没有插件/bot 响应，你可以视情况回复
{peer_cmd_list}
"""

    # 文化知识
    culture_section = ""
    if matched_culture:
        culture_str = "\n".join(f"- {t.get('term', '')}: {t.get('meaning', '')} ({t.get('context', '')})" for t in matched_culture)
        culture_section = f"## 相关文化知识\n以下是当前聊天中涉及的文化词汇：\n{culture_str}\n\n"

    # 组装 prompt
    prompt = f"你是 {bot_name}，{bot_role}\n\n"
    prompt += f"""## 回复风格
你是一个真实的群聊参与者，不是AI助手。
- 回复要像和人聊天一样自然、口语化
- 可以使用网络用语
- 回复长度要有变化
- 不需要对每条消息都认真回复
- 累了的时候可以稍微敷衍
若上述风格要求与你的知识有冲突，以你的知识为优先。

## 当前状态
社交能量：{_energy_description(social_energy)}

"""
    if preset_knowledge:
        prompt += "## 你的知识\n" + preset_knowledge + "\n\n"
    if culture_section:
        prompt += culture_section

    prompt += f"""## 你的记忆

### 短期记忆
```json
{short_term_str}
```
管理原则：优先修改，能改就不加；积极删除。
- modify：对话有新进展时直接更新
- delete：已结束的话题
- add：仅当列表中没有相关内容时才添加

### 长期记忆
```json
{long_term_str}
```
管理原则：优先修改，积极删除过时信息。

### 相关群友信息
```json
{friends_str}
```
fields: info(一般信息), aliases(称呼), nickname(QQ昵称), past_nicknames(曾用昵称)

## 理解群聊对话
- [回复 xxx 的消息: "yyy"] 表示回复
- @某人 表示发给那个人
- 如果消息没有@你，也没有回复你的消息，通常不是发给你的
- 不确定是否在和你说话 → 不回复

## 最近的聊天记录
{recent_str}

## 新消息
{new_msgs_str}{meme_section}{sticker_section}

## 已知群友昵称
{user_list_str}
{plugin_section}
---

你可以在回复的同时管理你的记忆。请输出 JSON：

```json
{{
  "reply": [
    {{ "type": "text", "content": "回复内容" }},
    {{ "type": "at", "name": "群友昵称" }},
    {{ "type": "meme", "id": "表情包id" }}
  ],
  "memory": {{
    "short_term": {{"add": [], "modify": [{{"index": 0, "content": ""}}], "delete": []}},
    "long_term": {{"add": [], "modify": [{{"index": 0, "content": ""}}], "delete": []}},
    "friends": {{"123456": {{"add": [], "modify": [], "delete": [], "add_alias": [], "remove_alias": []}}}},
    "save_meme": [{{ "id": "cache_id", "description": "描述", "keywords": ["关键词"] }}],
    "culture": {{"add": [{{"term": "", "meaning": "", "context": ""}}], "modify": [], "delete": []}}
  }},
  "command_learning": [{{"name": "命令名", "parameters": "可选参数说明(无则留空)", "usage": "命令 [参数]", "hook_type": "current或peer", "source": "current填插件名/peer填bot名", "examples": ["用户发送的原文"]}}],
  "command_edit": [{{"name": "命令名", "parameters": "更新后参数说明", "usage": "更新后用法", "examples": ["更新后示例"]}}],
  "command_delete": ["命令名"]
}}
```

> `command_learning` / `command_edit` / `command_delete` 是**可选**顶层字段：只在识别到新命令、需修正或删除已有命令时才输出，无相关操作时**整个字段省略**（`reply`、`memory` 各子项为空数组时也要保留外层结构）。

## 回复决策
- 有人 @ 你 → 通常回复
- 有人回复了你之前的消息 → 回复
- 消息是对所有人说的 → 通常不回复，能量较高时可根据兴趣决定
- 不确定是否在和你说话 → 不回复
- 社交能量低时 → 可以不回复或敷衍
- 不要对图片内容发表评论，除非群友@了你
- 合并聊天记录（[收到一条合并聊天记录]）→ 不回复，通常不是发给你的
- 小程序/卡片链接（[小程序/卡片: ...]）→ 不回复，通常不是发给你的

## 其他规则
- 不要编造表情包 id
- 绝对不要在 reply 正文中提及任何 id
- 你就是 {bot_name}，不要暴露自己是 AI
- friends 的 key 必须是 QQ 号（数字）
"""
    return prompt


def _bus_entry_text(msg: PluginMessage) -> str:
    """ContextBus 条目在聊天记录里的正文（与缓冲区侧的插件消息内容保持同形）"""
    return f"[图片] {msg.content}" if msg.message_type == "image" else msg.content


def _build_recent_str(
    recent: list[ChatMessage],
    bus_messages: list[PluginMessage],
    bot_name: str,
    window: float,
    new_messages: list[ChatMessage] | None = None,
) -> str:
    """合并最近消息和 ContextBus 消息，按时间排序（同一条插件响应只保留一份）

    插件输出会同时进入消息缓冲区与 ContextBus：仍在 bus 窗口内的以带来源标注的那份为准；
    已由本批「新消息」呈现过的，不再从 ContextBus 重复注入。
    """
    batch_keys = {(m.user_name, m.content) for m in (new_messages or [])}

    entries: list[tuple[datetime, str]] = []
    bus_keys: set[tuple[str, str]] = set()
    for msg in bus_messages:
        source = msg.source_plugin or "其它插件"
        text = _bus_entry_text(msg)
        if (source, text) not in batch_keys:
            entries.append((msg.timestamp, f"[{source}] {bot_name}: {text}"))
        bus_keys.add((source, text))

    for msg in recent:
        if (msg.user_name, msg.content) in bus_keys:
            continue
        entries.append((msg.time, f"{msg.user_name}: {msg.content}"))

    entries.sort(key=lambda x: x[0])
    return "\n".join(text for _, text in entries) or "无"


def _get_active_user_ids(messages: list[ChatMessage]) -> list[str]:
    seen = set()
    result = []
    for msg in messages:
        if msg.user_id and msg.user_id not in seen:
            seen.add(msg.user_id)
            result.append(msg.user_id)
    return result

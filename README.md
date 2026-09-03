# nonebot-plugin-aigf-master

群聊特化 LLM 聊天机器人，具备跨插件感知、插件调用、LLM 命令学习、跨 bot 通信等能力。

## 📖 介绍

一个基于 NoneBot2 的群聊 AI 助手插件，能够感知同实例内其它插件的输出、通过 LLM 调用其它插件、自主学习群内命令用法，并能通过 HTTP 与其它 bot 通信获取消息和远程调用命令。

### 核心能力

- 🔌 **跨插件感知**：LLM 能看到同实例内其它插件的输出（文本/图片）
- 🤖 **插件调用**：LLM 可通过 function calling 调用本机插件（`invoke_plugin`）或其它 bot 的插件（`invoke_peer_plugin`）
- 🧠 **LLM 命令学习**：通过观察群聊自动学习命令用法，支持学习、编辑、删除
- 🌐 **跨 bot 通信**：通过 HTTP 获取其它 bot（同一 QQ 号下）的插件输出，并可远程调用其命令

### 基础能力

- 🎭 **社交能量系统**：动态能量影响回复意愿
- 🧠 **四层记忆系统**：短期/长期/群友/文化记忆，LLM 自主管理
- 🔍 **联网搜索**：支持 Tavily / Bocha / Bing / open-webSearch
- 🖼️ **表情包功能**：AI 自主发表情包、自动收藏、按"旧且少用"清理
- 📨 **消息合并**：同一用户连续消息自动合并
- ⏱️ **不完整消息检测**：纯 @ 或连续发送时自动延长等待
- 🔒 **强制 JSON 输出** + 🧹 **Think 标签剥离**

## 💿 安装

```bash
pip install nonebot-plugin-aigf-master
```

在 `pyproject.toml` 中添加：

```toml
[tool.nonebot]
plugins = ["nonebot-plugin-aigf-master"]
```

## 配置

在 `.env` 中添加：

### 必填

```env
AIGFM_LLM_API_KEY="sk-xxxxxxxxxxxx"
AIGFM_LLM_BASE_URL="https://api.openai.com/v1"
AIGFM_LLM_MODEL="gpt-4o"
AIGFM_ENABLED_GROUPS=[123456, 789012]
```

### 常用可选

```env
# 批处理
AIGFM_BATCH_COUNT=10
AIGFM_BATCH_TIMEOUT=30.0
AIGFM_INCOMPLETE_TIMEOUT=40.0
AIGFM_RECENT_MESSAGES=20

# 表情包
AIGFM_MEME_ENABLED=true
AIGFM_MEME_MAX_COUNT=200

# 联网搜索（function_call 模式）
AIGFM_SEARCH_ENABLED=false
AIGFM_SEARCH_API=tavily              # tavily / bocha / bing / openwebsearch
AIGFM_SEARCH_API_KEY=""
AIGFM_OPENWEBSEARCH_URL=""           # openwebsearch 时填本地服务地址

# 图片理解
AIGFM_IMAGE_MODE="vlm"               # vlm / llm
AIGFM_VLM_ENABLED=true
AIGFM_VLM_MODEL=""
AIGFM_VLM_BASE_URL=""

# 跨 bot 通信（可选）
AIGFM_PEER_BOTS=[]                   # [{"name": "botB", "port": 8080, "token": "..."}]
```

## 命令（SUPERUSER）

| 命令 | 说明 |
|------|------|
| `status` / `状态` | 查看机器人状态 |
| `set_role <名字> <设定>` | 设置机器人角色 |
| `reset` / `重置` | 重置会话 |
| `presets` | 查看角色预设 |
| `set_preset <预设名>` | 加载预设 |
| `reload_meme` / `重载表情包` | 热重载表情包 |

## 跨 bot 通信

配合 `nonebot-plugin-aigfm-peer` 子插件（安装在其它 bot 上），通过 HTTP：
- 其它 bot 的插件输出推送到本插件，进入 LLM 上下文（标注 `[bot名]`）
- LLM 调用 `invoke_peer_plugin` 远程调用其它 bot 的命令

## License

AGPL-3.0

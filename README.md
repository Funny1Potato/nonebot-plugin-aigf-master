<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo"></a>

## ✨ AI-group-friend-master ✨

群聊特化 LLM 聊天机器人，具备表情包管理、记忆存储、联网搜索、跨插件感知、插件调用、LLM 命令学习等能力，并以适配器无关的方式收发消息（OneBot / Satori / Telegram / Discord 等 alconna 支持的适配器均可）。

<p>
    <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="python">
    <img src="https://img.shields.io/badge/nonebot-2.5+-red?style=flat-square" alt="nonebot">
</p>
</div>

> [!WARNING]
> **这是 beta 分支（2.0.0 开发中）：跨适配器改造版**，与 1.6.x（main）不兼容，请勿直接覆盖生产环境的稳定版：
> - 会话改用 `适配器:会话id` 为键（旧版 `memory/{群号}` 目录会在启动时**自动迁移**为 `onebot11_群号`），新增私聊会话支持
> - 新增依赖 `nonebot-plugin-alconna`（统一消息）与 `nonebot-plugin-uninfo`（跨适配器会话/成员信息）
> - 插件调用改为「复制真实消息事件」，合成事件里携带**真实发送者的昵称/群名片/角色**（见「无法代为执行的命令」）
>
> 稳定版请留在 main 分支（1.6.x），beta 的功能验证完成后再合并发版。

## 📖 介绍

一个基于 NoneBot2 的群聊 AI 助手插件，能够自主收集和发送表情包、联网搜索知识、对群内信息进行记忆，还能够感知同实例内其它插件的输出、通过 LLM 调用其它插件、自主学习群内命令用法，并能通过子插件与其它 bot 通信获取消息和远程调用命令。消息收发走 [nonebot-plugin-alconna](https://github.com/nonebot/plugin-alconna) 的通用消息层、会话与成员信息走 [nonebot-plugin-uninfo](https://github.com/nonebot/plugin-uninfo)，因此同一份代码可以在多个适配器上工作（详细边界见「多适配器支持」）。
> 本项目有AI高度参与，若有做得不够好及需要改进的地方，欢迎在issue提出意见。

### 核心能力

- 🧩 **多适配器支持**：消息收发与会话身份走通用层，OneBot / Satori / Telegram / Discord 等适配器共用同一套聊天、记忆、表情包与命令逻辑（跨插件感知、插件调用等能力仍以 OneBot 为主，见下文边界）
- 🔌 **跨插件感知**：LLM 能看到同实例内其它插件的输出（如 rollpig 的小猪卡片、搜索结果等）
- 🤖 **插件调用**：LLM 可通过 function calling 调用其它插件（如 `/roll`、`/天气`），支持传递参数与 @ 群友（如 `决斗 @张三 10`）
- 🧠 **LLM 命令学习**：通过观察群聊自动学习命令用法，支持学习、编辑、删除
- 🌐 **跨 bot 通信**：通过 HTTP 获取其它 bot（同一 QQ 号下）的插件输出，并可远程调用其命令
- 🖼️ **表情包功能**：AI 自主决定发表情包；自动从群聊中收藏
- 🔍 **联网搜索**：支持 Tavily / Bocha / Bing，自动搜索不懂的梗和网络用语
- 🧠 **四层记忆系统**：短期记忆、长期记忆、群友信息、文化记忆，LLM 自主管理
- 🎨 **AI 生图**：LLM可调用生图模型生成图片发到群里，尺寸由 LLM 决定（程序限最大尺寸），支持参考图改绘（回复图片消息或从聊天记录选图）

### 基础能力

- 🎭 **社交能量系统**：动态能量影响回复意愿，形成自然的"话多→累了→安静→恢复"周期
- 💬 **口语化回复**：像平时聊天一样自然，回复长度有变化，允许敷衍
- 📨 **消息合并**：同一用户连续消息自动合并，避免碎片化上下文
- ⏱️ **不完整消息检测**：纯 @ 消息或连续发送时自动延长等待时间
- 🕐 **消息时间标注**：可选为 LLM 看到的消息加上 `[MM-DD HH:MM]` 时间前缀（`AIGFM_SHOW_MESSAGE_TIME`）
- 📄 **完整消息类型**：合并聊天记录、小程序/卡片、XML，以及 QQ 表情/语音/视频/群文件/链接分享/推荐名片/位置/音乐/骰子等共 18 种消息段均可解析（纯此类消息不再被丢弃）
- 🔔 **群系统通知**：戳一戳、禁言/解禁、新人进群、退群被踢、消息撤回会以 `[系统]` 消息进入 LLM 上下文
- 🔒 **强制 JSON 输出**：确保 LLM 始终返回正确格式
- ⚡ **轻量高效**：单次 LLM 调用完成对话 + 记忆管理 + 命令学习

## 💿 安装

> [!IMPORTANT]
> 要使用本插件, 你至少需要
>
> - 一个有效的 openai 规范接口 api key，你需要在 `.env` 文件中配置对应的 api 地址
>
> 从 2.0 起插件还依赖 `nonebot-plugin-alconna` 与 `nonebot-plugin-uninfo`（跨适配器收发与会话/成员信息），用下面的方式安装时会自动装上。

### 安装 beta 版本（2.0.0b0）

`2.0.0b0` 是**预发布（pre-release）版本**，因此：

- `pip install nonebot-plugin-aigf-master` 装的仍是稳定版 **1.6.x**
- `nb plugin install nonebot-plugin-aigf-master` 同样只装稳定版，而且 nb-cli **不支持指定版本**，所以 beta 只能用 pip 装

要装 beta，**必须显式写出版本号**：

```bash
pip install nonebot-plugin-aigf-master==2.0.0b0
```

在 `pyproject.toml` 中添加：

```toml
[tool.nonebot]
plugins = ["nonebot-plugin-aigf-master"]
```

> 需要装 beta 分支上尚未发版的改动时，可以直接从分支安装：
> `pip install git+https://github.com/Funny1Potato/nonebot-plugin-aigf-master.git@beta`


## 配置

在 `.env` 或 `.env.prod` 中添加：

### 必填

```env
# --- 基础设置 ---
AIGFM_LLM_API_KEY="sk-xxxxxxxxxxxx"                               # LLM API Key
AIGFM_LLM_BASE_URL="https://api.deepseek.com"                     # LLM API 地址
AIGFM_LLM_MODEL="deepseek-v4-flash"                               # LLM 模型名称
AIGFM_ENABLED_GROUPS=[123456, 789012]                             # 启用的群/频道会话（可写 `适配器:会话id`，只写数字按 OneBot V11 识别）
AIGFM_ENABLED_PRIVATE=[]                                          # 启用的私聊会话（同上写法；留空则私聊不响应）

# --- 图片理解（VLM） ---
AIGFM_IMAGE_MODE="vlm"                  # 图片模式: vlm / llm（默认 vlm）
AIGFM_VLM_ENABLED=true                  # 是否启用VLM（仅 vlm 模式有效，默认 true）
AIGFM_VLM_MODEL="..."                   # VLM 模型名称（vlm 模式必填）
AIGFM_VLM_BASE_URL="https://..."        # VLM API 地址
AIGFM_VLM_API_KEY=""                    # VLM API Key（为空时使用 llm 的 key）
```

### 可选

```env
# --- AI 生图 ---
AIGFM_IMAGE_GEN_ENABLED=false          # 是否启用 AI 生图（默认 false）
AIGFM_IMAGE_GEN_MODEL="..."            # 生图模型名称
AIGFM_IMAGE_GEN_BASE_URL="https://..." # 生图 API 地址
AIGFM_IMAGE_GEN_API_KEY=""             # 生图 API Key（为空时使用 chat 的 key）
AIGFM_IMAGE_GEN_MAX_SIZE="1024x1024"   # 生成最大尺寸限制（宽x高，LLM 可指定比例/尺寸，程序自动缩放到不超过该上限）
AIGFM_IMAGE_GEN_MIN_SIZE=""            # 生成最小尺寸（宽x高，为空不限制；用于满足部分模型最低像素要求，如豆包 Seedream 需 "1920x1920"，非空时按比例放大到不小于该面积）
AIGFM_IMAGE_GEN_WATERMARK=false        # 生图水印（默认 false）

# --- 基础 ---
AIGFM_MEME_ENABLED=true                 # 是否启用表情包功能（默认 true）
AIGFM_MEME_MAX_COUNT=200                # 自动收集的表情包最大数量（默认 200）
AIGFM_DEFAULT_PRESET=default            # 默认预设名称（默认 "default"）

# --- 请求控制 ---
AIGFM_BATCH_COUNT=10                    # 攒满多少条消息后触发 LLM 请求（默认 10）
AIGFM_BATCH_TIMEOUT=30.0                # 距最后一条消息多少秒后触发（默认 30.0）
AIGFM_INCOMPLETE_TIMEOUT=40.0           # 消息可能不完整时的等待时间/秒（默认 40.0；替代默认 30s 生效，非叠加）
AIGFM_RECENT_MESSAGES=20                # prompt 中包含的最近历史消息条数（默认 20，最大 50）
AIGFM_MERGE_WINDOW=5.0                  # 消息合并时间窗口/秒（默认 5.0）
AIGFM_SHOW_MESSAGE_TIME=false           # LLM 看到的消息是否带 [MM-DD HH:MM] 时间前缀（默认 false）

# --- LLM ---
AIGFM_LLM_JSON_MODE=true                # 是否强制 LLM 输出 JSON（默认 true）
AIGFM_LLM_TOOLS_JSON_STRICT=false       # 工具调用路径首次请求也强制 JSON 输出（默认 false；开启后"未调用工具直接回复"的那一轮同样输出 JSON。部分 OpenAI 兼容服务不支持 tools+json_object 组合，开启前请确认服务商支持）
AIGFM_ENERGY_BASELINE=0.7               # 社交能量基线（默认 0.7，范围 0.0~1.0）

# --- 联网搜索 ---
AIGFM_SEARCH_ENABLED=false              # 是否启用联网搜索（默认 false）
AIGFM_SEARCH_API=tavily                 # 搜索API: tavily / bocha / bing / openwebsearch（默认 tavily）
AIGFM_SEARCH_API_KEY="xxx"              # 搜索 API Key（api=tavily/bocha/bing 时必填）
AIGFM_SEARCH_MAX_RESULTS=3              # 最大搜索结果数（默认 3）
AIGFM_OPENWEBSEARCH_URL=""              # open-websearch 本地服务地址（api=openwebsearch 时必填，如 http://127.0.0.1:3210）

# --- 代理 ---
AIGFM_PROXY_ENABLED=false                  # 是否启用代理（默认 false）
AIGFM_HTTP_PROXY="http://127.0.0.1:7890"   # HTTP 代理地址
AIGFM_HTTPS_PROXY="http://127.0.0.1:7890"  # HTTPS 代理地址

# --- 跨插件感知 / 插件白名单 ---
AIGFM_CAPTURE_PLUGINS=[]                # 插件白名单（捕获+命令扫描+调用核对 共用）：非空=只捕获/扫描这些插件且只允许调用它们；空=捕获所有但不扫描静态命令、不核对插件归属（自家管理命令仍被拒；LLM 只应调用清单内命令）
AIGFM_CAPTURE_IMAGES=true               # 是否捕获并解析图片（默认 true；同时作用于本机插件输出与 peer 推送）
AIGFM_CONTEXT_IN_PROMPT=10              # 注入到 prompt 中的其它插件消息条数（默认 10）
AIGFM_STICKER_CACHE_MAX_FILES=300       # 图片缓存文件（sticker_cache）最大数量，超出按最旧删除（默认 300）

# --- 插件调用 ---
AIGFM_INVOKE_ENABLED=true               # 是否允许 LLM 调用其它插件（默认 true）
AIGFM_INVOKE_TIMEOUT=30.0               # 插件调用超时时间（秒，默认 30.0）
AIGFM_INVOKE_DEDUP_ENABLED=true         # 拒绝调用最近调用过的相同命令（默认 true；命令+参数+调用身份完全相同）
AIGFM_INVOKE_DEDUP_WINDOW=1200.0        # 去重时间窗口（秒，默认 1200 = 20 分钟；0 = 不限时间，只看最近 N 条台账）
AIGFM_INVOKE_DEDUP_RECORDS=15           # 去重比对保留的调用台账条数（默认 15；prompt 里只展示其中最近 10 条）
AIGFM_INVOKE_ENERGY_COST=0.1            # 每次调用插件消耗的社交能量（默认 0.1；0 表示不消耗）
AIGFM_INVOKE_ENERGY_MIN=0.4             # 社交能量低于此值时拒绝调用插件（默认 0.4；0 表示不限制）

# --- 命令学习 ---
AIGFM_LEARN_COMMANDS=true               # 是否通过群聊学习未注册的命令（默认 true）
AIGFM_LEARN_MIN_CONFIDENCE=3            # 命令学习的最小置信度（默认 3）

# --- 跨 bot 通信 ---
AIGFM_PEER_BOTS=[]                      # 其它 bot 列表，每项含 name/port/token，如:
                                        # [{"name": "botB", "port": 8080, "token": "aaa114514"}]

# --- 二次元角色识别（可选，默认关闭） ---
AIGFM_ANIME_BACKEND=off                 # 角色识别后端：off（默认，关闭）/ anime-recognize（本地 WD14 服务）/ animetrace（AnimeTrace 公共 API）/ both（本地优先，置信度不足再问 AnimeTrace）
AIGFM_ANIME_RECOGNIZE_URL=""            # 本地识别服务地址（backend 含 anime-recognize 时必填，如 http://127.0.0.1:8000）
AIGFM_ANIME_RECOGNIZE_TOKEN=""          # 本地识别服务 Bearer token（服务端未启用鉴权时留空）
AIGFM_ANIME_RECOGNIZE_MIN_CONFIDENCE=0.85  # 展示角色标签的最低置信度（默认 0.85；both 模式下也用它判断本地是否有结果）
AIGFM_ANIME_RECOGNIZE_MAX_CHARACTERS=3  # 展示的角色标签数量上限（默认 3）
AIGFM_ANIME_NSFW_THRESHOLD=0.5          # explicit 概率超过该值标注 [NSFW]（默认 0.5；仅本地后端提供画风分级）
AIGFM_ANIMETRACE_URL="https://api.animetrace.com"  # AnimeTrace API 地址（公共 API、无需鉴权；识别会把图片上传到该服务）
AIGFM_ANIMETRACE_TIMEOUT=20.0           # AnimeTrace 请求超时/秒（默认 20.0）

# --- 缓存清理 ---
AIGFM_IMAGE_CACHE_MAX_FILES=500         # 描述/角色识别缓存（image_cache 下的 JSON）最大数量，超出按 mtime 最旧删除（默认 500，0=不清理）
AIGFM_RAW_CACHE_MAX_FILES=200           # 原始图片缓存（raw/，按 fileid 命名）最大数量，超出按 mtime 最旧删除（默认 200，0=不清理）
```

### 其它 bot 配置（安装子插件 [nonebot-plugin-aigfm-peer](https://github.com/Funny1Potato/nonebot-plugin-aigfm-peer)）

在其它 bot 的 `.env` 中配置：

```env
AIGFM_PEER_PUSH_PORT=14514             # 主插件（aigf-master所在bot）的 HTTP 端口
AIGFM_PEER_TOKEN="aaa114514"           # 与 AIGFM_PEER_BOTS 中对应项的 token 一致
AIGFM_PEER_BOT_NAME="botB"             # 本 bot 名称（与 AIGFM_PEER_BOTS 的 name 对应）
AIGFM_PEER_CAPTURE_PLUGINS=[]          # 要捕获输出的插件名列表，为空则捕获所有
```

## 命令

| 命令 | 说明 | 权限 |
|------|------|------|
| `status` / `状态` | 查看机器人状态（角色、社交能量、最近消息） | SUPERUSER |
| `set_role <名字> <设定>` | 设置机器人角色 | SUPERUSER |
| `reset` / `重置` | 重置会话：清空近期聊天记录与其它插件的输出缓冲（ContextBus），恢复默认预设与社交能量。**不删除**持久化的四层记忆与已学命令 | SUPERUSER |
| `presets` | 查看可用的角色预设 | SUPERUSER |
| `set_preset <预设名>` | 加载指定的角色预设 | SUPERUSER |
| `reload_meme` / `重载表情包` | 热重载表情包配置 | SUPERUSER |

## 触发机制

- 攒够 **10 条**（可配置）新消息，或最后一条消息后 **30 秒**（可配置）内无新消息，触发一次处理
- 纯 @ 消息或检测到同一用户连续发送时，以及调用的插件返回图片时，等待时间会用 `AIGFM_INCOMPLETE_TIMEOUT`（默认 40 秒）**替代**默认的 30 秒（非叠加）
- **有图片正在解析时整批推迟触发**：等到 VLM 描述填好后随**同一批**发给 LLM，不会把没有描述的空消息发出去；"图片进入解析"这一特例最长等待 `AIGFM_BATCH_TIMEOUT + AIGFM_INCOMPLETE_TIMEOUT`（默认 70 秒）后仍会放行，此时该图片以 `[图片解析中]` 进入上下文
- 消息合并窗口内的同一用户连续消息会被合并为一条
- 每次处理时，LLM 收到最近 **20 条**（可配置）历史聊天记录 + 四层记忆 + 预设 + 表情包列表 + 其它插件响应；本批消息只出现在「新消息」段，同一条插件响应也只注入一次（不会在历史与新消息里重复）
- LLM 一次调用同时完成：回复决策 + 记忆管理 + 表情包选择 + 命令学习

## 🧩 多适配器支持

插件的消息**收发**（文本、@、图片、语音、视频、群文件、链接分享、回复、合并转发等）走 [nonebot-plugin-alconna](https://github.com/nonebot/plugin-alconna) 的通用消息层，**会话与成员信息**（昵称、群名片、角色、群名、@ 目标解析）走 [nonebot-plugin-uninfo](https://github.com/nonebot/plugin-uninfo)。因此只要适配器被这两个库支持，聊天 / 记忆 / 表情包 / 生图 / 命令学习等功能就能直接工作，不需要为每个适配器改代码。

### 会话与启用清单

- 会话以 **`适配器:会话id`** 为键，例如 `onebot11:617770183`（群）、`console:console-chat`（频道）、`onebot11:20001`（私聊）
- `AIGFM_ENABLED_GROUPS` 管群/频道会话、`AIGFM_ENABLED_PRIVATE` 管私聊会话；条目可写带前缀的完整键，也可以只写 id（**按 OneBot V11 识别**，旧配置无需改动）
- 落盘目录名不能含 `:`，因此磁盘上写作 `onebot11_617770183`；`memory/` 下旧版裸数字目录会在启动时**一次性自动迁移**（目标已存在则跳过，幂等）
- 非启用会话不发请求、不入缓冲：插件只会为启用的会话解析成员信息与图片

### 功能 × 适配器支持表（2026-09-25 实测）

测试方式：本机用 `nb`/pip 装上各适配器包后，为每个适配器**构造真实事件**（各适配器的消息/事件模型），跑本插件同一套功能路径——会话解析（uninfo）→ 消息渲染（uniseg）→ 回复发送（alconna exporter）→ 跨插件捕获（`on_calling_api`）→ 插件调用（复制真实事件 + 目标响应器实际收到命令）。bot 为记录型 mock（拦下所有 API 调用并断言调用的接口名），**未连真实平台**，因此"需要真实 API 才有数据"的项目另标 ⚠。

| 适配器 | 会话解析 | 收发/渲染 | 回复发送 | 私聊 | @ 按昵称 | 跨插件捕获 | 插件调用 | 群通知¹ | 跨 bot² |
|---|---|---|---|---|---|---|---|---|---|
| OneBot V11 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| OneBot V12 | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Console | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Satori | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ⚠ | ❌ | ⚠ |
| Telegram | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Discord | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| QQ（频道/C2C/群） | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Feishu | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Milky | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Mirai | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Kaiheila（Kook） | ✅ | ✅ | ✅ | 未测³ | ⚠ | ✅ | ⚠ | ❌ | ⚠ |
| DoDo | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Kritor | ✅ | ✅ | ✅ | 未测³ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Mail | ✅（仅私聊） | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| Minecraft | ✅（仅私聊） | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| WXMP | ✅（仅私聊） | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| EFChat | ✅ | ✅ | ✅ | ✅ | ⚠ | ✅ | ✅ | ❌ | ⚠ |
| YunHu | ⚠ 需 Python ≥3.12⁴ | ⚠ 同左 | ⚠ 同左 | ⚠ 同左 | ⚠ | ⚠ 同左 | ⚠ 同左 | ❌ | ⚠ |
| bilibili Live | ⚠ 需 Python ≥3.12⁴ | ⚠ 同左 | ⚠ 同左 | ❌ 无好友私聊类型 | ⚠ | ⚠ 同左 | ⚠ 同左 | ❌ | ⚠ |

**读表说明**

- **✅ 已实测通过**：会话键、渲染文本、发送接口、捕获入缓冲、目标插件确实收到了命令，逐项断言过（共 165 项检查）
- **⚠ 需真机/特例**：
  - **@ 按昵称**：只有 OneBot V11 实测能按群昵称反查出用户 id（走 `get_group_member_list`）；其它适配器需要 uninfo 的成员列表查询实现（多数适配器没有），因此**让 LLM 直接用用户 id 更稳**；渲染 @ 时昵称拿不到会回落成 id
  - **插件调用（Satori / Kaiheila）**：这两类适配器的事件结构特殊（Satori 的 `message` 是 `{id, content}` 结构体、Kaiheila 的消息在嵌套的 `event` 里），离线构造"uninfo 与 alconna 都认可"的事件未能复现，因此**未验证通过**；其余 14 个适配器已实测目标响应器收到命令。真机使用请以实际表现为准
  - **跨 bot**：需要 peer 0.4.0+（推送/调用带 `session` 键）；旧 peer 只能按 `onebot11:{group_id}` 落到 OneBot 会话
- **❌ 不支持**：群系统通知¹（戳一戳/禁言/进出群/撤回是 OneBot 专属事件类型）
- **⚠ 需要 Python ≥ 3.12**（上表 YunHu / bilibili Live）：这两个适配器包在 3.11 及以下装不上/用不了——`bilibili Live` 用了 `typing.TypedDict`（pydantic 在 Python < 3.12 直接拒绝），`YunHu` 还额外用到 `typing.NotRequired`（Python 3.11+）。**已在 Python 3.14.3 上实测**：两个包都能导入，`alconna` 的 builder/exporter 与 `uninfo` 的 fetcher 三者齐备；`bilibili Live` 的弹幕事件跑通了会话解析（场景路径＝房间号）+ 渲染 + 通用化，`YunHu` 的群消息事件同样能跑通渲染与通用化（会话解析那步需要真实 bot API，mock 下拿不到群资料）。想在本机测这两个平台，用 `py -3.14` 单独建个环境即可（本机已有一个 `C:\Users\94512\.qwen\tmp\py314test` 作为参考）
- **Kook 注意**：社区包 `nonebot-adapter-kook` 的模块名是 `nonebot.adapters.kook`、`get_name()` 报 `Kook`，而 alconna/uninfo 期望的是 `nonebot.adapters.kaiheila`（`Kaiheila`）——**请安装 `nonebot-adapter-kaiheila`**，否则该平台等同于"不被支持"
- **昵称/群名片**：取决于适配器的 uninfo fetcher 能取到什么（很多要真实 API 调用，离线 mock 拿不到），拿不到时回落成用户 id，功能不受影响
- 适配器完全不被 alconna/uninfo 支持时，该会话的消息会被静默跳过（只留 debug 日志），不会报错、也不会影响其它适配器

<details>
<summary>实测到的各适配器发送接口（供排查用）</summary>

| 适配器 | 回复发送最终调用的接口 |
|---|---|
| OneBot V11 / V12 | `send_msg` / `send_message` |
| Console | `send_message`（私聊 `send_private_message`） |
| Satori | `send_message`（私聊 `send_private_message`） |
| Telegram | `send_to` |
| Discord | `send_to` |
| QQ | `send_to_group` / `send_to_channel` / `send_to_c2c` |
| Feishu | `send_msg` |
| Milky | `send_group_message` / `send_private_message` |
| Mirai | `send_group_message` / `send_friend_message` |
| Kaiheila | `send_msg`（按 channel/private 区分） |
| DoDo | `send_to_channel` / `send_to_personal` |
| Kritor | `send_message` / `send_channel_message` |
| Mail | `send_to` |
| Minecraft | `send_msg` |
| WXMP | `send_custom_message` |
| EFChat | `send_chat_message` |

</details>

> ¹ 群系统通知 = 戳一戳/禁言/进出群/消息撤回。
> ² 跨 bot = 子插件 [nonebot-plugin-aigfm-peer](https://github.com/Funny1Potato/nonebot-plugin-aigfm-peer) 的推送与远程调用。
> ³ Kritor / Kaiheila 的私聊事件本次未构造，故未测；它们的私聊类型在 alconna/uninfo 里是有实现的。
> ⁴ 这两个适配器**需要 Python ≥ 3.12**，见下方说明（本机 Python 3.10 环境下无法导入，故未做逐项实测；已在 3.14 上验证可导入且依赖齐备）。

## 🔌 跨插件感知

### 工作原理

```
其它插件发消息
    ↓
自动捕获插件输出
    ├─ 文本 → 直接进入上下文
    └─ 图片 → 先转成文字描述再进入上下文
    ↓
LLM 在聊天记录中看到：[插件名]: 插件的输出
```

### 示例

用户发送 `今日小猪`，rollpig 插件响应后（下一批处理时，「新消息」段的实际渲染）：

```
张三: '今日小猪'
[rollpig]: '[图片] 这张图片展示了一个卡通形象：一只粉红色的猪...'
```

群友消息为 `名字: '内容'`，插件/bot 来源的消息带 `[来源]` 前缀；这些响应若还在更早的批次里出现，则在「最近的聊天记录」段以 `[插件名] 机器人名字: 内容` 形式呈现。LLM 能看到完整的交互过程。

### 可捕获的消息类型

本插件可捕获并注入 LLM 上下文的插件/bot 输出消息类型：

| 类型 | 处理方式 |
|------|---------|
| 文本 | 直接以 `[来源]: '内容'` 进入上下文 |
| 图片 | 先经 VLM 转成文字描述，再以 `[图片] 描述` 进入上下文（`AIGFM_CAPTURE_IMAGES=false` 时不捕获） |
| 合并聊天记录 | 以 `[收到一条合并聊天记录]` 进入上下文 |
| 小程序/卡片 | 以 `[小程序/卡片: 标题, 描述]` 进入上下文 |
| XML | 以 `[收到一条XML消息]` 进入上下文 |

图片的来源字段依次支持：

| 来源 | 说明 |
|------|------|
| `url` | 网络图片地址 |
| `file`（`http://` / `https://` 开头） | 以 url 形式给出的 file 字段（如 `MessageSegment.image("https://...")`） |
| `file`（`file://` 开头） | 本地文件路径（如 `MessageSegment.image(Path(...))`，转 base64 后解析） |
| `file`（`base64://` 开头） | base64 编码的图片 |
| `base64` | 直接的 base64 图片数据 |

本机插件的输出（`api_hooks` 钩子捕获）与其它 bot 的推送（peer 子插件捕获）走**同一套**图片解析逻辑；`AIGFM_CAPTURE_IMAGES` 开关同时作用于两者。

群消息本身支持解析的消息类型：文本、图片/表情包、@、引用回复（`[回复 xxx 的消息: "yyy"]`）、合并聊天记录（`[收到一条合并聊天记录]`）、小程序/卡片（`[小程序/卡片: 标题, 描述]`）、XML（`[收到一条XML消息]`）。

## 🤖 插件调用

LLM 可通过 function calling 调用插件，有两个工具：
- **`invoke_plugin`**：调用**本机**插件
- **`invoke_peer_plugin`**：调用**其它 bot** 上的插件（需指定 `bot` 名）

```
用户：@小助手 帮我摇一个今日小猪
    ↓
LLM 调用：invoke_plugin(command="今日小猪")
    ↓
自动分发给 rollpig 插件执行
    ↓
响应作为新消息进入缓冲
    ↓
下一批处理时 LLM 看到：[用户] 命令 → [插件] 图片响应
    ↓
LLM 基于插件响应回复
```

> 调用时可通过 `user_id` 参数指定以哪位群友的身份调用（从"相关群友信息"中选择 QQ 号），**不同 QQ 号调用可能得到不同结果**。

### 命令参数与 @（parts）

需要带参数的命令用 `parts` 传参数段（结构与回复的 `reply` 字段相同）：

```
LLM 调用：invoke_plugin(command="决斗", parts=[
    {"type": "at", "name": "张三"},
    {"type": "text", "content": "10"}
])
    ↓
实际发出的消息：决斗 @张三 10
```

- `command` **只放命令名本身**；要 @ 的群友、附加文字等一律放进 `parts`
- `at` 段按**群昵称**解析（与聊天记录里的 `@昵称` 一致），也接受直接给 QQ 号；解析不出会跳过该段，并在工具回执里提示
- 各段之间由程序补**一个**空格，不需要也不用自己加空格（多写的空格会被规整，避免出现双空格导致参数解析失败）

### 调用限制

三条代码层限制，用于防止重复执行与滥用：

| 限制 | 默认 | 配置项 |
|---|---|---|
| **重复调用去重** | 开启 | `AIGFM_INVOKE_DEDUP_ENABLED` |
| **去重时间窗口** | 20 分钟 | `AIGFM_INVOKE_DEDUP_WINDOW`（秒；0 = 不限时间） |
| **去重比对条数** | 15 | `AIGFM_INVOKE_DEDUP_RECORDS` |
| **每次调用消耗能量** | 0.1 | `AIGFM_INVOKE_ENERGY_COST`（0 = 不消耗） |
| **能量门槛** | 低于 0.4 拒绝 | `AIGFM_INVOKE_ENERGY_MIN`（0 = 不限制） |

- 去重判定为**命令 + 参数 + 调用身份（QQ 号）三者完全相同**；拦截范围 = **时间窗口内**（`AIGFM_INVOKE_DEDUP_WINDOW`）∩ **调用台账保留的最近 N 条**（`AIGFM_INVOKE_DEDUP_RECORDS`）
- **不区分本机与其它 bot**：同一命令、同一身份在范围内只执行一次
- 换个参数（`决斗 @张三` → `决斗 @李四`）或换个身份（不同 QQ 号）视为不同调用，正常放行
- prompt 里的「你最近代为执行过的命令」段只展示**其中最近 10 条**（防止 prompt 随条数膨胀）
- 执行 `/reset` 会一并清空调用台账（重置后不再被"刚调用过"挡住）
- 每次**实际投递**扣 `AIGFM_INVOKE_ENERGY_COST` 点社交能量（被拒绝时不扣）；能量会按社交能量机制自然回充，所以能量不足只是暂时无法代为执行命令
- 被拒绝时：工具结果**当场**告知 LLM 原因，同时在聊天记录里留下 `已拒绝调用命令「xxx」（原因）`，**下一轮** LLM 能看到；拒绝**不写入调用台账**（不会阻止之后真正的再调用）

### 命令前缀自动适配

- LLM 调用命令时**始终传不带前缀的命令**（如 `今日小猪`）
- 执行时自动按各 bot 的 `COMMAND_START` 配置补充前缀：
  - `COMMAND_START=[""]` → 命令无前缀
  - `COMMAND_START=["/"]` → 自动补 `/`（如 `/今日小猪`）
- 本地与 peer 插件均按各自所在 bot 的配置适配前缀

### 插件白名单（`AIGFM_CAPTURE_PLUGINS`）

一个白名单同时控制三件事；peer 子插件对称使用 `AIGFM_PEER_CAPTURE_PLUGINS`：

| 白名单 | 捕获输出 | 静态命令扫描/上报 | 调用核对 |
|---|---|---|---|
| 非空 | 只捕获这些插件的输出 | 只扫描/展示这些插件的命令 | 只允许调用这些插件，其余拒绝 |
| 空 | 捕获所有插件输出 | 不扫描静态命令（学习命令仍注入） | 不核对插件归属；但本插件自身的管理命令（状态/重置/改角色等）仍会被直接拒绝 |

> 设计意图：代理/感知范围与可调用范围通常是同一批插件，合并为一个白名单简化配置。

### 只能调用清单中的命令

LLM 只能调用 prompt 里「可用的群功能」/「其它 bot 的命令」清单中**逐字出现**的命令：

- 清单为空时（`AIGFM_CAPTURE_PLUGINS` 未设置且还没学到命令），prompt 仍会注入「功能调用的边界」段，要求此时不要使用调用工具
- 本插件自身的管理命令（`status`/`状态`、`reset`/`重置`、`set_role`、`presets`、`set_preset`、`reload_meme` 等）**在投递前就被拒绝**，不会由 LLM 代为执行
- `save_meme`、`memory`、`reply` 等是 JSON **输出字段名，不是命令**；收藏表情包只能通过 `memory.save_meme` 字段完成
- 工具返回「命令已投递」只代表事件已发出，**不代表执行成功**；只有后续聊天记录里出现 `[插件名]: '...'`（新消息段）或 `[插件名] 机器人名字: ...`（历史段）的响应才算成功

### 无法代为执行的命令

`invoke_plugin` / `invoke_peer_plugin` 并不是让真人发消息，而是**复制该会话里最近一条真实消息事件**、把消息体换成命令文本后投递（2.0 起改为这种做法，此前是手工拼一个 `GroupMessageEvent`）。因此事件的适配器字段、发送者信息都来自那条真实消息，比过去准确得多，但仍与"真的有人发了这条命令"有差别，下列命令**调不动**（表现为投递后聊天记录里始终不出现该插件的响应）：

| 命令类型 | 无法执行的原因 |
|---|---|
| 需要 @机器人 或呼叫昵称才触发 | 合成消息里没有 @机器人，`to_me` 沿用被复制那条消息的值（通常为 `False`） |
| 私聊命令 / 与事件类型强相关 | 复制的是群/频道事件（`message_type` 保持原样），不会命中只监听私聊或其它事件类型的 Matcher |
| 必须先回复/引用某条消息才能执行 | 合成消息不含回复段，事件里的 `reply` 也被清空 |
| 依赖群名片 / 头衔 / 等级 / 地区判断身份 | 发送者昵称、群名片、角色已按真实身份填好（`refresh` 不到的字段仍为空：头衔、等级、地区等） |
| 需要特殊 `message_id` 语义的操作 | `message_id` 是**被复制那条消息的真实 id**（不再是占位 `0`）：引用回复可用，但插件若对"这条消息"做撤回/编辑等操作，作用的其实是那条真实消息 |

关于**权限**：合成事件的发送者是**提出请求的那位群友**（`user_id`、昵称、群名片、角色都按真实身份填），所以：

- 需要管理员/群主权限的命令，**请托者本身有相应权限时可以被执行**（1.6.x 及更早版本里 `role` 固定为 `member`，一律拒绝）；请托者没有权限时仍会被目标插件拒绝
- `SUPERUSERS` 相关的命令同理：以请托者的身份通过检查
- 本插件自己的管理命令（`status`/`reset`/`set_role`/…）**无论谁请托都一律拒绝**，不会代为执行（避免真实用户恰好是 SUPERUSER 时被 LLM 触发）

两点补充：

- `on_keyword` / `on_regex` / `on_message` 型插件（不用 `on_command` 注册）**不会出现在静态命令列表**里，无法被自动发现；但它们的 rule 只匹配文本时，合成事件仍会命中，所以可以靠 LLM 命令学习掌握后调用。
- 命令学习会**主动跳过**上表中无法代为执行的命令（不会为它们生成用法记录），避免 LLM 反复误调用。

> 需要多步确认的命令（执行中等待用户再回复）不在上表内：会话按适配器的会话 id 归组（OneBot V11 为 `group_{group_id}_{user_id}`），只要后续真实消息来自同一个 `user_id`，等待流程仍能接上。

> 若目标插件是用 alconna 写的，插件会在投递前把 alconna 按 `message_id` 缓存的那条消息**替换成命令内容**——否则它会把命令读成"原来那条用户消息"，永远匹配不上。

## 🌐 跨 bot 通信

利用子插件 [nonebot-plugin-aigfm-peer](https://github.com/Funny1Potato/nonebot-plugin-aigfm-peer) 通过 HTTP 与其它 bot 通信，实现：
1. **消息获取**：其它 bot 的插件输出推送到本插件，进入 LLM 上下文（标注 `[bot名]`）
2. **远程插件调用**：LLM 调用其它 bot 的命令，路由到对应 bot 远程执行

### 工作原理

```
其它 bot 的插件发消息
    ↓
子插件 nonebot-plugin-aigfm-peer 捕获消息并推送到本插件
    ├─ 文本 → 直接加入消息缓冲
    └─ 图片 → VLM 描述后加入消息缓冲
    ↓
下一批处理时 LLM 看到：[bot名] 响应

LLM 要调用其它 bot 的命令时：
指定目标 bot 和命令 → 发送到该 bot 执行
    → 响应推回本插件 → LLM 下一批看到
```

### 部署

1. 在其他bot上安装 [nonebot-plugin-aigfm-peer](https://github.com/Funny1Potato/nonebot-plugin-aigfm-peer)
2. 在本插件 `.env` 配置 `AIGFM_PEER_BOTS`，在其它 bot `.env` 配置推送端口/token/bot名
3. 两端 token 必须一致

> 说明：peer 命令可由 LLM 命令学习自动发现，无需手动配置命令列表。

## 🧠 LLM 命令学习

### 工作原理

```
用户发命令 → 其它插件响应
    ↓
LLM 在聊天记录中同时看到"命令"和"插件的响应"
    ↓
判断这是命令 → 自动学习（记录命令、参数、用法、示例）
```

### 命令管理

LLM 可以：
- **学习新命令**：记录命令、可选参数、使用方法、示例
- **编辑命令**：修正参数、用法或示例
- **删除命令**：移除错误或过时的命令（需达到阈值才会真正删除）
- **跳过不可调用的命令**：需要 @/昵称触发、需要管理权限、私聊专用、依赖引用消息等命令不会被学习（详见「无法代为执行的命令」）；这类命令仍是命令，有插件响应时 LLM 依旧不抢答

### 命令静默

- 有插件对命令做出响应 → LLM 不回复（避免重复）
- 没有插件响应 → LLM 可视情况回复（如告知命令无响应）

## 🎭 社交能量系统

机器人拥有动态的"社交能量"（范围 0.0~1.0，初始 0.75）：

| 能量范围 | 状态描述 | 表现 |
|---------|---------|------|
| ≥ 0.8 | 精力充沛 | 看到什么都想插嘴 |
| ≥ 0.6 | 状态不错 | 有兴趣的话题会主动参与 |
| ≥ 0.4 | 一般般 | 有人找就回，不太主动 |
| ≥ 0.2 | 有点懒 | 倾向于潜水 |
| < 0.2 | 不想说话 | 完全不想说话 |

- 每次消息处理时自然恢复（向基线 0.7 靠拢）+ 随机漂移（±0.08）
- 被 @ 时兴奋加成 +0.1
- 回复后消耗能量：基础消耗 0.03 + 按回复文字长度增加
- 形成自然的"话多→累了→安静→恢复→又想聊"周期

## 🔍 联网搜索系统

启用后，LLM 通过 **function calling** 自主决定是否调用 `search_internet` 工具搜索。

### 搜索 API

| API | 说明 | 额外依赖 | 获取 Key / 部署 |
|-----|------|---------|----------------|
| `tavily`（默认） | 专为 AI 设计，返回格式友好 | `tavily-python` | https://app.tavily.com |
| `bocha` | 国产 AI 搜索 API，中文搜索效果好 | 无 | https://open.bochaai.com |
| `bing` | 微软必应搜索（Azure Bing Search API v7） | 无 | https://portal.azure.com |
| `openwebsearch` | 本地 Node.js 搜索服务，聚合多引擎（bing/baidu/duckduckgo 等） | 需先启动 daemon：`cd open-webSearch && npm install && npm run serve`，配置 `AIGFM_OPENWEBSEARCH_URL` | 无需 Key，项目：https://github.com/Aas-ee/open-webSearch |

> **Tavily** 需额外安装依赖：`pip install tavily-python`（或安装本插件时带上 extra：`pip install "nonebot-plugin-aigf-master[tavily]"`）

## 🧠 记忆系统

机器人拥有四层记忆，由 LLM 在每次回复时自主管理：

### 短期记忆

存储在 `<插件数据目录>/memory/<群号>/short_term.json`，内容为 LLM 维护的信息列表，包括对话摘要、临时上下文、有趣的梗等。LLM 可以添加、修改、删除条目。

### 长期记忆

存储在 `<插件数据目录>/memory/<群号>/long_term.json`，内容为 LLM 认为值得长期记住的信息，如群内发生的事件、群规、群友分享的有用知识等。LLM 可添加、修改、删除。不应记录临时对话或常识信息。

### 群友信息

存储在 `<插件数据目录>/memory/friends/<QQ号>.json`，每个群友一个文件，以 QQ 号命名。LLM 记录群友的昵称、职业、爱好、说过的话、与其他群友的关系等。

| 字段 | 来源 | 说明 |
|------|------|------|
| `nickname` | 系统自动更新 | QQ 全局昵称 |
| `aliases` | LLM 管理 | 群友对 ta 的称呼 |
| `past_nicknames` | 系统自动记录 | 曾用 QQ 昵称 |
| `info` | LLM 管理 | 一般信息（职业、爱好等） |
| `groups` | 系统自动维护 | 所在的群列表 |

### 文化记忆

存储在 `<插件数据目录>/memory/<群号>/culture.json`，记录梗、网络用语、流行语。LLM 主动学习和存储，根据聊天内容自动匹配。

## 🖼️ 表情包功能

### 工作原理

```
群聊中有人发图片/表情包
    ↓
下载图片 → VLM 分析内容和情感
    ↓
保存到缓存目录（<缓存目录>/sticker_cache/）
    ↓
下一次消息处理时，LLM 在 Prompt 中看到缓存的表情包
    ↓
LLM 决定是否收藏 → 保存到 memes 目录
```

### 表情包素材库

存放在 `<插件数据目录>/memes/` 下：

```
memes/
├── memes.json          ← 管理员手动配置
├── collected.json      ← 机器人自动收集
└── *.jpg/png/gif       ← 表情包图片文件
```

#### 管理员手动配置

编辑 `memes.json`：

```json
[
  {
    "id": "happy_spin",
    "path": "happy_spin.jpg",
    "keywords": ["开心", "高兴", "庆祝"],
    "description": "开心到转圈的小人"
  }
]
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | ✅ | 唯一标识符，AI 用这个选择表情包 |
| `path` | ✅ | 图片文件名（相对于 memes 目录） |
| `keywords` | ✅ | 适用场景关键词 |
| `description` | ✅ | 一句话描述内容 |

修改后执行 `/重载表情包` 即可生效，无需重启。

#### 自动收集

机器人收到图片时，VLM 分析后保存到缓存。LLM 在回复时看到缓存的表情包，决定是否收藏。

- 图片按 MD5 hash 去重
- 群聊图片的缓存 id 会跨批次保留，所以**历史消息里的图片也能补收藏**（照抄聊天记录中的 `[发送了一张图片, id: xxx]` 即可）；执行 `/reset` 会清空这批缓存索引
- 缓存条目数受 `AIGFM_CONTEXT_MAX_MESSAGES`（每群）与 `AIGFM_STICKER_CACHE_MAX_FILES`（磁盘文件总数，按最旧删除）双重限制，不会无限增长
- 每个表情包记录**使用次数**和**保存时间**
- 超过 `AIGFM_MEME_MAX_COUNT` 上限时，按**归一化加权**清理：`保存时间旧 + 使用次数少 → 优先删除`，最近发过的表情包不会删除

## 📷 图片理解模式

| 模式 | 流程 | 适用场景 |
|------|------|---------|
| `vlm`（默认） | 图片 → VLM 分析 → 文字描述给 LLM | LLM 不支持图片输入 |
| `llm` | 图片 → base64 直接附在 LLM prompt 中 | LLM 支持视觉 |

`vlm` 模式下的行为说明：

- 图片内容来源依次支持 `url`、`file`（含 `http…`、`file://…`、`base64://…`）、`base64` 三种字段；群消息、其它插件输出、peer 推送都走同一套解析
- 描述与情感两次请求**并发**发出；普通图片（非表情包）不再请求情感，只跑一次 VLM
- VLM 请求跑到**自身超时**（`VLMClient` 写死 60 秒）为止，不再受 `AIGFM_INCOMPLETE_TIMEOUT` 限制；超时/失败/`AIGFM_VLM_ENABLED=false` 时群消息以 `[发送了一张图片]（识图失败）`、其它插件与 peer 推送的图片以 `[图片] （识图失败）` 进入上下文（消息不会被丢弃）
- 解析未完成时该批会被推迟（见「触发机制」），因此慢 VLM 会推迟该群的回复；相同图片内容有 md5 结果缓存，命中即秒回
- 描述/角色识别缓存（`image_cache/*.json`）与原始图片缓存（`raw/`）只增不减，超过 `AIGFM_IMAGE_CACHE_MAX_FILES`（默认 500）/ `AIGFM_RAW_CACHE_MAX_FILES`（默认 200）时按 mtime 最旧删除（启动时 + 每次写入后检查；删掉只意味着下次重新识别/重新下载）

## 🔮 二次元角色识别（可选，默认关闭）

识别动漫 / 二次元手游 / gal 游戏角色。提供两种后端，可任选其一或组合使用：

| 后端 | 工作方式 | 擅长 | 代价 |
|---|---|---|---|
| `anime-recognize` | 本地 WD14 推理服务（[aigfm-anime-recognize](https://github.com/Funny1Potato/aigfm-anime-recognize)），**需自行部署** | 动漫 / 手游角色，输出英文 booru 标签（如 `hu_tao_(genshin_impact)`）+ 数值置信度 + 画风分级 | 本地推理、免费无限量；**对 gal / 视觉小说角色识别率低** |
| `animetrace` | [AnimeTrace](https://ai.animedb.cn/) 公共 API，**无需部署、无需鉴权** | gal / 视觉小说角色，并给出作品名（如「胡桃（原神）」） | 图片会上传到该第三方公共服务；有使用配额；不返回数值置信度、无画风分级；对gal以外游戏角色的识别率一般 |

`both` 模式先问本地服务，本地置信度不足（低于 `AIGFM_ANIME_RECOGNIZE_MIN_CONFIDENCE`）再问 AnimeTrace，两边都没有可信结果才渲染 `[角色识别: 未能识别]`——平时不消耗公共 API 配额。

**架构**：插件进程内不加载任何模型——本地后端走 HTTP 调用独立部署的服务，AnimeTrace 走公共 API。

**部署本地识别服务**（[aigfm-anime-recognize](https://github.com/Funny1Potato/aigfm-anime-recognize)，用 `animetrace` 后端时不需要）：

```bash
git clone https://github.com/Funny1Potato/aigfm-anime-recognize.git
cd aigfm-anime-recognize
# 安装
# Windows: install.bat ；Linux/macOS: chmod +x install.sh && ./install.sh
# 启动
# Windows: start.bat ; Linux / macOS: chmod +x start.sh && ./start.sh
```

**插件配置**：

```env
# 后端选择：off（默认，关闭）/ anime-recognize（本地）/ animetrace（公共 API）/ both（本地优先，不足再问 AnimeTrace）
AIGFM_ANIME_BACKEND=both

# 本地后端地址（backend 含 anime-recognize 时必填；服务端未启用鉴权时 token 留空）
AIGFM_ANIME_RECOGNIZE_URL="http://127.0.0.1:8000"
# AIGFM_ANIME_RECOGNIZE_TOKEN=可选

# 可选调参（默认值如下）
# AIGFM_ANIME_RECOGNIZE_MIN_CONFIDENCE=0.85   # 展示角色标签的最低置信度；both 模式下也用它判断本地是否有结果
# AIGFM_ANIME_RECOGNIZE_MAX_CHARACTERS=3      # 展示的角色标签数量上限
# AIGFM_ANIME_NSFW_THRESHOLD=0.5              # explicit 概率超过该值标注 [NSFW]（只标注不拦截；仅本地后端提供分级）
# AIGFM_ANIMETRACE_URL="https://api.animetrace.com"
# AIGFM_ANIMETRACE_TIMEOUT=20                 # AnimeTrace 请求超时（秒）
```

> `AIGFM_ANIME_RECOGNIZE_ENABLED` 已被 `AIGFM_ANIME_BACKEND` 取代，但为兼容老配置仍然生效：它为 true 且 `AIGFM_ANIME_BACKEND` 未设（`off`）时按 `anime-recognize` 处理。

**工作流程**：开启后，VLM 描述 prompt 会要求"二次元风格加（二次元）标记"——只有描述带该标记的图片才触发识别（普通照片/截图不会触发）。AnimeTrace 的模型列表在启动时查询一次（`/v1/model/list` 取 default，不写死模型名；服务报参数错会自动重查）。识别结果渲染在图片描述后：

```
[发送了一张图片, id: xxx] [内容:红发双马尾少女的立绘] [角色识别: hu_tao_(genshin_impact) 98.7%]
[发送了一张图片, id: xxx] [内容:…] [NSFW] [角色识别: skadi_(arknights) 99.5%]
[发送了一张图片, id: xxx] [内容:…] [角色识别: 胡桃（原神）]      ← 本地认不出、AnimeTrace 认出（角色（作品））
[发送了一张图片, id: xxx] [内容:…] [角色识别: 未能识别]        ← 两种后端都没有可信结果（新角色/冷门）
```

- 多人同图（图中 N 个角色、识别出的少于 N 个时）会追加 `（图中检测到 N 个角色，仅识别出部分）`
- 后端调用失败/超时（含 AnimeTrace 配额用尽、服务繁忙）→ 静默降级，不影响 VLM 描述；只要有任一后端成功应答但无可信结果，才会出现 `[角色识别: 未能识别]`
- 结果按图片内容 md5 缓存（本地与 AnimeTrace 各一份），同一张图不重复识别、不重复消耗公共 API 配额
- **动图（GIF）取首帧**转 JPEG 后送识别（本地服务内部同样取首帧，AnimeTrace 不接受 GIF），缓存键仍是原图
- 改 `AIGFM_ANIME_RECOGNIZE_MIN_CONFIDENCE` 只影响此后新识别的图片；已缓存图片要重新判定需删除其 `{md5}_anime.json`

**已知局限**：
- **本地后端**：训练数据截止 2024 年前后，新游/新角色可能认不出，**gal / 视觉小说角色识别率尤其低（建议改用 `animetrace` 或 `both`）**；多人合影可能会漏识别；3D 战斗小人/游戏内截图识别率不高；输出为英文标签（中文映射需自行扩展）
- **AnimeTrace**：对gal以外的游戏角色识别率一般；不返回数值置信度，只有服务自己的低置信判定——被判低置信时一律按「未能识别」处理，日志 `[角色识别] AnimeTrace 无可信结果，低置信候选: …` 会给出它认为最可能的候选，便于排查；其检测框可能包含误检，多人提示仅供参考；`[NSFW]` 标记只由本地后端提供
- 识别结果（角色名）会进入 LLM 上下文，也就是群里能看到；在意隐私请只用 `anime-recognize`（纯本地）或关闭本功能

> 仅 `AIGFM_IMAGE_MODE=vlm` 模式生效；`llm` 模式图片不经过 VLM，无二次元判断。

## 🎭 预设系统

首次运行后在 `<插件配置目录>/presets/` 下生成 `default.json`：

```json
{
  "name": "小助手",
  "role": "一个友好的群聊助手",
  "knowledges": [],
  "hidden": false
}
```

在 `presets/` 目录下创建新的 JSON 文件即可添加新预设，执行 `set_preset <预设名>` 加载。

## 📨 消息格式

LLM 支持以下回复类型：

| 类型 | 格式 | 说明 |
|------|------|------|
| 文本 | `{"type": "text", "content": "..."}` | 纯文本消息 |
| @ | `{"type": "at", "name": "群友昵称"}` | 艾特群友 |
| 表情包 | `{"type": "meme", "id": "表情包id"}` | 发送表情包 |

## 📁 数据存储

```
{data_dir}/
  memory/
    {group_id}/
      short_term.json       # 短期记忆
      long_term.json        # 长期记忆
      culture.json          # 文化记忆
    friends/
      {user_id}.json        # 群友信息
  memes/
    memes.json              # 管理员表情包索引
    collected.json          # 自动收集表情包索引（含 usage_count/saved_at）
    {hash}.{ext}            # 表情包文件
  learned_commands.json     # 当前实例学习到的命令
  peer_commands.json        # 其它 bot 的命令

{cache_dir}/
  image_cache/
    {md5}.json              # 图片 VLM 描述缓存（开启角色识别时另有 {md5}_anime.json 描述缓存、{md5}_anime_recognize.json 与 {md5}_animetrace.json 识别结果缓存）
                            # 清理：超过 AIGFM_IMAGE_CACHE_MAX_FILES（默认 500）时按 mtime 最旧删除，启动时与每次写入描述缓存后各检查一次
  raw/{fileid}              # 原始图片缓存（按消息 url 里的 fileid 命名，与 image_cache 同级）
                            # 清理：超过 AIGFM_RAW_CACHE_MAX_FILES（默认 200）时按 mtime 最旧删除，启动时与每次写入后各检查一次
  sticker_cache/
    {hash}.{ext}            # 图片/表情包缓存文件

{config_dir}/
  presets/
    default.json            # 默认预设
    *.json                  # 自定义预设
```

## 依赖

```
必需：nonebot2, nonebot-adapter-onebot, nonebot-plugin-localstore,
     openai, httpx, anyio, pillow, pydantic, numpy
可选：tavily-python（Tavily 搜索）
```

## 兼容性

- **Function Calling**：插件调用和搜索需要模型支持 tool_call
- **插件调用范围**：命令通过合成事件投递，无法代为执行的命令类型见「无法代为执行的命令」

## License

MIT

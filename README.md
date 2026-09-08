<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo"></a>

## ✨ AI-group-friend-master ✨

群聊特化 LLM 聊天机器人，具备表情包管理、记忆存储、联网搜索、跨插件感知、插件调用、LLM 命令学习等能力。

<p>
    <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="python">
    <img src="https://img.shields.io/badge/nonebot-2.3+-red?style=flat-square" alt="nonebot">
</p>
</div>

## 📖 介绍

一个基于 NoneBot2 的群聊 AI 助手插件，能够自主收集和发送表情包、联网搜索知识、对群内信息进行记忆，还能够感知同实例内其它插件的输出、通过 LLM 调用其它插件、自主学习群内命令用法，并能通过子插件与其它 bot 通信获取消息和远程调用命令。

### 核心能力

- 🔌 **跨插件感知**：LLM 能看到同实例内其它插件的输出（如 rollpig 的小猪卡片、搜索结果等）
- 🤖 **插件调用**：LLM 可通过 function calling 调用其它插件（如 `/roll`、`/天气`）
- 🧠 **LLM 命令学习**：通过观察群聊自动学习命令用法，支持学习、编辑、删除
- 🌐 **跨 bot 通信**：通过 HTTP 获取其它 bot（同一 QQ 号下）的插件输出，并可远程调用其命令
- 🖼️ **表情包功能**：AI 自主决定发表情包；自动从群聊中收藏
- 🔍 **联网搜索**：支持 Tavily / Bocha / Bing，自动搜索不懂的梗和网络用语
- 🧠 **四层记忆系统**：短期记忆、长期记忆、群友信息、文化记忆，LLM 自主管理

### 基础能力

- 🎭 **社交能量系统**：动态能量影响回复意愿，形成自然的"话多→累了→安静→恢复"周期
- 💬 **口语化回复**：像平时聊天一样自然，回复长度有变化，允许敷衍
- 📨 **消息合并**：同一用户连续消息自动合并，避免碎片化上下文
- ⏱️ **不完整消息检测**：纯 @ 消息或连续发送时自动延长等待时间
- 🔒 **强制 JSON 输出**：确保 LLM 始终返回正确格式
- ⚡ **轻量高效**：单次 LLM 调用完成对话 + 记忆管理 + 命令学习

## 💿 安装

> [!IMPORTANT]
> 要使用本插件, 你至少需要
>
> - 一个有效的 openai 规范接口 api key，你需要在 `.env` 文件中配置对应的 api 地址

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令安装（测试中，还未上架）

    nb plugin install nonebot-plugin-aigf-master --upgrade

</details>

<details>
<summary>使用包管理器安装</summary>

```bash
pip install nonebot-plugin-aigf-master
```

在 `pyproject.toml` 中添加：

```toml
[tool.nonebot]
plugins = ["nonebot-plugin-aigf-master"]
```

</details>


## 配置

在 `.env` 或 `.env.prod` 中添加：

### 必填

```env
# --- 基础设置 ---
AIGFM_LLM_API_KEY="sk-xxxxxxxxxxxx"                               # LLM API Key
AIGFM_LLM_BASE_URL="https://api.deepseek.com"                     # LLM API 地址
AIGFM_LLM_MODEL="deepseek-v4-flash"                               # LLM 模型名称
AIGFM_ENABLED_GROUPS=[123456, 789012]                             # 启用的群号列表

# --- 图片理解（VLM） ---
AIGFM_IMAGE_MODE="vlm"                  # 图片模式: vlm / llm（默认 vlm）
AIGFM_VLM_ENABLED=true                  # 是否启用VLM（仅 vlm 模式有效，默认 true）
AIGFM_VLM_MODEL="..."                   # VLM 模型名称（vlm 模式必填）
AIGFM_VLM_BASE_URL="https://..."        # VLM API 地址
AIGFM_VLM_API_KEY=""                    # VLM API Key（为空时使用 chat 的 key）
```

### 可选

```env
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

# --- LLM ---
AIGFM_JSON_MODE=true                    # 是否强制 LLM 输出 JSON（默认 true）
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

# --- 插件调用 ---
AIGFM_INVOKE_ENABLED=true               # 是否允许 LLM 调用其它插件（默认 true）
AIGFM_INVOKE_TIMEOUT=30.0               # 插件调用超时时间（秒，默认 30.0）

# --- 命令学习 ---
AIGFM_LEARN_COMMANDS=true               # 是否通过群聊学习未注册的命令（默认 true）
AIGFM_LEARN_MIN_CONFIDENCE=3            # 命令学习的最小置信度（默认 3）

# --- 跨 bot 通信 ---
AIGFM_PEER_BOTS=[]                      # 其它 bot 列表，每项含 name/port/token，如:
                                        # [{"name": "botB", "port": 8080, "token": "aaa114514"}]
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

`invoke_plugin` / `invoke_peer_plugin` 是构造一个**合成群事件**（`GroupMessageEvent`）把命令文本投递给目标插件，而不是让真人发消息。合成事件与真实消息存在字段差异，下列命令**调不动**（表现为投递后聊天记录里始终不出现该插件的响应）：

| 命令类型 | 无法执行的原因 |
|---|---|
| 需要 @机器人 或呼叫昵称才触发 | 合成事件的 `to_me` 恒为 `False`（该字段由适配器在真实消息上计算，投递时绕过了这一步） |
| 需要管理员 / 群主 / 超级用户权限 | `sender.role` 固定为 `"member"`，且传入的 `user_id` 一般不在 `SUPERUSERS` 内，权限检查直接拒绝 |
| 私聊命令 | 合成事件固定 `message_type="group"`，不会命中只监听私聊的 Matcher |
| 必须先回复/引用某条消息才能执行 | 合成事件的 `reply` 恒为 `None`，且真实消息里的 `reply` 段会被适配器展开，这里没有 |
| 依赖群名片 / 头衔 / 等级 / 地区判断身份 | `sender` 只填 `user_id`、`nickname`（固定 `"aigf_user"`）、`role`，其余字段为空 |
| 依赖真实 `message_id` 的后续操作 | `message_id` 是占位值 `0`，插件拿它去 `get_msg` / 撤回 / 引用回复会失败 |

两点补充：

- `on_keyword` / `on_regex` / `on_message` 型插件（不用 `on_command` 注册）**不会出现在静态命令列表**里，无法被自动发现；但它们的 rule 只匹配文本时，合成事件仍会命中，所以可以靠 LLM 命令学习掌握后调用。
- 命令学习会**主动跳过**上表中无法代为执行的命令（不会为它们生成用法记录），避免 LLM 反复误调用。

> 需要多步确认的命令（执行中等待用户再回复）不在上表内：会话按 `group_{group_id}_{user_id}` 归组，只要后续真实消息来自同一个 `user_id`，等待流程仍能接上。

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
    {md5}.json              # 图片 VLM 描述缓存
  raw/{fileid}              # 原始图片缓存（按消息 url 里的 fileid 命名，与 image_cache 同级）
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

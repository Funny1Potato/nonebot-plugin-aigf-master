# 当前状态

核对日期：2026-11-14。本体 2.0.0b5 已核对 GitHub Release、发布工作流和 PyPI 下载包；其余版本/分支来自本地 Git。已完成隔离真实模型及非聊天平台实服务验证，尚未验证真实聊天服务或长期运行。

beta 源码包含 b5 之后尚未发布到 PyPI 的改动：会话协议覆盖、运行诊断、peer 请求关联及 CI 发布门槛。使用与边界见 [运维说明](OPERATIONS.md)。当前源码 35 个 Python 文件，b5 tag/安装包仍为 34 个；不能将工作区新能力当作 b5 已发布功能。

## 代码位置与版本

| 对象 | 本地状态 |
| --- | --- |
| 本体 b5 发布源码 | beta，tag `v2.0.0b5`，提交 `d9069d3`，pyproject `2.0.0b5` |
| 本体发布状态 | GitHub 预发布已创建，Actions 成功；PyPI wheel/sdist 已下载核对，与 b5 提交一致 |
| 本体 main | 本地 `f9f88c8`，版本 `1.6.4`；本次没有切换或修改 main |
| peer 当前 checkout | beta，pyproject `0.4.0b1`；包含尚未发版的捕获修复与请求关联，提交以 Git 为准 |
| peer 远端跟踪状态 | 与本地 origin/beta 一致 |
| 运行副本 | 本体 35 个、peer 2 个 Python 文件与各自仓库一致（忽略 CRLF/LF）；包含 b5 后本地开发改动 |

该表是接手快照，不是永久版本声明。每次发版、切分支或源码同步后应更新；日常以 Git 和 pyproject 为准。

## 当前 beta 功能

| 能力 | 当前状态与边界 |
| --- | --- |
| 群聊/频道/私聊 | 跨适配器会话；私聊默认关闭，启用清单独立配置 |
| 回复决策 | 批处理、角色预设、社交能量、按配置选择旧 JSON 回复或多轮工具动作 |
| 记忆 | 短期、长期、群友、文化；legacy_json 通过最终 JSON 修改；tools 通过版本化工具修改；recent.json 落盘 |
| 图片 | 默认多模态 LLM，支持能力探测/回落；可配置独立 VLM；角色识别双后端、缓存清理 |
| 表情包 | 手工库、自动收藏、发送；临时图片缓存支持跨批引用 |
| 搜索/生图 | Tavily、Bocha、Bing、DeepSeek、openwebsearch；OpenAI 兼容生图接口 |
| 本机插件 | 捕获输出、命令发现与学习、复制真实事件调用；有白名单、去重、能量和每批共享调用额度限制；明确区分投递回执状态 |
| peer | HTTP 捕获推送与远程命令调用，session 字段与旧 group_id 兼容 |
| 通知 | 已通用化；各适配器能否定位会话与渲染仍有差异 |
| 工具回合 | legacy_json 保留单轮工具后最终 JSON；tools 为有预算的多轮执行，不发布结束文本 |

已实现（需显式启用 tools）：多轮框架、send_message、query_memory/update_memory、save_meme、query_commands/manage_command。默认仍使用 legacy_json；隔离真实模型检查已补充，真实会话灰度尚未进行。
旧 `beta-legacy` 的图片库、成员资料查询、图片元数据编辑等能力不在当前 beta 中，不能按旧文档宣称支持。

## 架构和数据流

用户消息 → 会话预筛/解析 → 文本与图片解析 → 每会话缓冲/批处理 → processor 构建 prompt → 按批选择动作协议。legacy_json 解析最终 JSON；tools 通过多轮显式工具修改存储或发送。两种协议统一通过 MessageSender 和 uniseg 发送。

- `__init__.py`：依赖组装、入口、批处理与管理命令。
- `processor.py`：编排、工具执行、能量、调用台账。
- `prompt_builder.py` / `response_parser.py`：无状态构建与解析。
- `session.py` / `unimsg.py` / `notice.py`：跨适配器身份、消息、通知。
- `api_hooks.py` / `context_bus.py`：同实例插件输出进入上下文。
- `plugin_invoker.py` / `peer_client.py`：真实事件投递、HTTP 远程调用。
- 各 store：记忆、表情包、预设与持久化。

插件和 peer 的输出进入后续上下文；调用已投递并不等于执行成功。只有插件/通知输出的会话可能等待首条真实用户消息后才能处理。

## 文档与验证边界

仓库 README 面向使用者；本文描述当前实现；DEVELOPMENT.md 说明维护流程；tests/README.md 与 BASELINE.md 说明可复现验证及结果。
历史交接保留在运行工作区 docs/archive，仅供追溯。适配器离线矩阵不等于真实平台联调结果；不要将历史计数直接作为当前通过证明。

以下按开发日期保留历史记录；旧段落的“本地未发布”“未联调”仅描述当时状态，当前验证与交付状态以本页最新记录为准。

## 2026-11-12：调用链路加固（当时本地未发布）

新增 `aigfm_invoke_max_per_batch`，默认每批 1 次，本机与 peer 共用，JSON 解析重试不重置。未投递不扣能量、不记去重；已投递/已接收/结果不确定保留去重。批处理固定调用身份与事件，工具入口不再依赖搜索开关。
本机请求编号用于日志与回执核对；尚未实现跨 peer 输出的请求关联或执行完成确认。新离线套件 `v_invocation_test.py` 覆盖这些行为，验证记录见 tests/BASELINE.md。

## 同日后续：适配器缺口修复（本地未发布）

Satori / Kaiheila 消息复制修复同时落在本体与 peer；消息/调用离线矩阵 206 项通过（含工具 sender）。通知显式会话回落补齐飞书、Kritor、bilibili Live，Discord / EFChat 的测试 mock 问题已修正；通知矩阵 15 OK、2 N/A。服务器级事件不自动进入子频道，完整说明见 [ADAPTER_SUPPORT.md](ADAPTER_SUPPORT.md)。

设计与执行记录：[全工具调用协议方案](TOOL_ONLY_AGENT_PLAN.md)；首版本地实现见 [工具协议说明](TOOL_PROTOCOL.md)。

## 2026-11-13：全工具协议首版（本地未发布）

本体新增 7 个模块，源码共 34 个 Python 文件。新协议为显式开关 tools，默认 legacy_json；旧发送和新发送共用 sender。工具的记忆修改采用 id/revision 与原子提交，群友按适配器区分；效果日志支持重启诊断且不自动重放。
记忆锁暂采用数据根级别的粗粒度单进程协调，跨适配器验证仍是离线 mock；补充的隔离真实模型重复与故障检查修复后 7 类 17 次通过，保留历史错误记录，见 [记录](LIVE_TOOL_SMOKE.md)，未进行生产灰度；不改变版本号、不推送、不重启机器人。

## 同日后续：模拟会话验证

自行构造两个 OneBot 会话，真实模型两轮检查通过，每轮含 12 项主流程与 3 项新进程恢复验证。覆盖完整入口、合批、原生 @、交错会话、测试迁移备份、原位记忆纠错和 reset。新增离线套件在 3.10 仓库源码、3.14 运行副本通过；没有连接真实聊天平台或重启生产机器人。入口与边界见 [SIMULATED_SESSION.md](SIMULATED_SESSION.md)。

## 同日后续：工作区实际启动与外部连接

已使用 aigf-manager 的 .venv、运行副本和模型配置启动 FastAPI/Uvicorn，执行实际项目插件生命周期；独立进程模拟外部 OneBot，通过反向 WebSocket 连接。脚本化模型、真实模型各 20 项检查通过，包含鉴权、网络收发、原生 @、隔离、测试迁移、reset、正常关闭和重新启动恢复。本次覆盖的是隔离配置下的实际驱动与网络链路，仍未连接真实聊天服务；没有改真实配置或业务数据。详见 [CONNECTED_PLATFORM.md](CONNECTED_PLATFORM.md)。

## 同日后续：非聊天平台实服务联调

用户更换搜索与生图配置后，实际双实例联调 16 项检查通过：本机 echo、peer HTTP 调用/输出回传、DeepSeek 搜索、MiniMax-H3-Image 后台生图。搜索独立查询得到 2 条结果，图像实际生成 2560×2560 且发回原会话。修复本体/peer 捕获钩子的查询 API 重入风险，以及搜索异常被当作空结果成功的问题；均已同步运行副本。

本体 3.10 默认 13/13、3.14 含矩阵 15/15；peer 两种环境各 2/2 通过。真实记忆副本离线迁移检查通过，源文件未改。没有真实聊天平台或长期运行验收；API 更换由用户操作，测试未改 .env*。详情见 [LIVE_SERVICES.md](LIVE_SERVICES.md)。

## 2026-11-13：2.0.0b5 已发布

已创建 [GitHub 预发布](https://github.com/Funny1Potato/nonebot-plugin-aigf-master/releases/tag/v2.0.0b5)，发布说明非空；[发布工作流](https://github.com/Funny1Potato/nonebot-plugin-aigf-master/actions/runs/43396623135) 成功，[PyPI 2.0.0b5](https://pypi.org/project/nonebot-plugin-aigf-master/2.0.0b5/) 可用。下载校验 wheel/sdist 哈希，并逐一对比 wheel 中的 34 个 Python 模块与 tag 提交。详情见 [发布核验记录](releases/2.0.0b5-verification.json)。

默认仍为 legacy_json，tools 需显式启用；尚未完成真实聊天服务与 24 小时灰度。peer 的本地修复没有在本次发布中提交、推送或发版，需要独立处理。本页较早的“本地未发布”小节保留为开发历史，当前本体状态以此发布记录为准。

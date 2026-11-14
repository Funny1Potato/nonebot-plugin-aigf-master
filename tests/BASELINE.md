> 最新结果见文末「适配器缺口修复」；早先失败和通过记录按时间保留。

# 离线验证基线：2026-11-12

代码基线：本体 beta `a7c7836`（2.0.0b5），本轮仅加入文档/测试/包发现配置，业务源码未修改。
环境：Windows，本工作区 .venv（Python 3.10）与 .venv314（Python 3.14）。本次复用已有依赖环境，未在新环境验证安装；适配器版本见 requirements-matrix.txt，基础依赖以 pyproject 约束为准。

## 默认回归

| 脚本 | 断言数 | Python 3.10 | Python 3.14 |
| --- | --- | --- | --- |
| v_anime_chain_test | 69 | 通过 | 通过 |
| v_beta_port_test | 43 | 通过 | 通过 |
| v_cache_cleanup_test | 17 | 通过 | 通过 |
| v_ma_test | 150 | 通过 | 通过 |
| v_notice_test | 64 | 通过 | 通过 |
| v_parser_test | 32 | 通过 | 通过 |
| v_recent_persist_test | 26 | 通过 | 通过 |
| v_search_test | 31 | 通过 | 通过 |

两种环境均 8/8 套件退出码为 0。迁移时修正 parser mock 的 multimodal 属性；为脚本显式清理事件循环，修复首轮部分脚本断言全过但 Windows 退出码为 0xC0000008 的现象。没有放宽业务断言。

## 扩展适配器诊断（Python 3.14）

- 消息/调用矩阵：187 个检查点，2 个失败：Satori、Kaiheila 的插件调用投递后目标响应器未命中。
- 通知矩阵：17 个检查点，10 个 OK、2 个 N/A、5 个失败。Discord、Feishu、Kritor、bilibili Live 的通知无法定位会话；EFChat 被判为私聊。
- 对应脚本均返回非零；运行 `--matrix` 的总体结果是 8/10 套件通过，不能称为全通过。
- 原打印表会把私聊平台的未执行子项显示成 FAIL，已改为 `-`，不改变 JSON 结果或检查点计数。
- 没有本次改动前同环境的重新跑测证据，因此不将上述缺口直接判为业务回归；它们是当前观察结果。离线配方局限与真实平台问题需要下一阶段分别定位。

可追踪快照：[消息与调用](baselines/2026-11-12-adapter_matrix.json)、[通知](baselines/2026-11-12-notice_matrix.json)。详细原始日志保存在本机 tests/.results 下，不提交日志目录。

## 打包与同步

本体和 peer 均使用 `pip wheel --no-deps --no-build-isolation --no-index` 本地构建成功，并检查 wheel 中不包含 tests/docs。初次构建因沙箱临时目录的文件重命名权限失败，改用仓库 tests/.results/build-temp 作为 TEMP/TMP 后成功。
运行副本与仓库本体 26 个、peer 2 个 Python 文件一致（忽略行尾）。未启动 bot、未调用真实 LLM/平台、未验证远端发布状态。

依赖版本快照：[Python 3.10](baselines/2026-11-12-env-py310.json)、[Python 3.14](baselines/2026-11-12-env-py314.json)。快照用于复查版本，不替代可安装的锁文件。

## 同日后续：插件调用可靠性加固

本轮业务改动为本地未发布版本（pyproject 仍为 2.0.0b5），已同步运行副本。新增 v_invocation_test.py，包含 16 个 unittest 场景；在 Python 3.10 和 3.14 下通过。已有 8 个默认套件两种环境全部通过，现在默认共 9 个套件。

新增场景覆盖：本机/peer 共用额度、不同命令/参数不能绕过、配置额度与新批重置、解析重试保留额度、固定身份与 @ 解析、明确未投递不扣能量且允许后续重试、结果不确定保持去重、取消传播、客户端异常、peer HTTP/连接/读写超时分类、搜索关闭时插件/生图工具入口、旧调用器返回码兼容。

Python 3.14 重跑扩展矩阵：消息/调用仍 187 项、2 项失败，通知仍 17 项、5 项失败；逐项结果类别与本页最初基线一致。整体 --matrix 为 9/11 套件通过，已有适配器缺口未修复，也无新增结果类别变化。

请求编号只关联本机日志、回执与台账，尚不关联 peer 回传输出；没有将投递回执升级为执行完成证明。没有真实平台或真实 LLM 联调。

## 同日后续：适配器缺口修复

改动仍为本地未发布版本，已同步运行副本，没有真实平台联调。

本体：Python 3.14 下默认套件加适配器矩阵共 13/13 通过（默认 11 个），Python 3.10 默认 11/11 通过。事件复制套件在两种环境追加并通过真实 alconna 目标响应器命中验证，共 3 个 unittest；通知回落套件 3 个 unittest 通过。
peer：默认 2 个套件在两种环境通过（既有 67 个断言；新事件复制套件 3 个 unittest）。

消息/调用矩阵 187 个检查点、0 失败；通知矩阵 17 项中 15 OK、2 N/A、0 失败。测试纠正：强引用单个 Alconna 探针；动态 API 使用配置返回值，保留同步方法；补齐 EFChat 配置和 Kaiheila 频道模型字段。生产修复：两侧复制逻辑更新 Satori eager cache 与 Kaiheila 原生 Message 槽位；本体按精确字段回落 Feishu/Kritor/bilibili Live 通知会话。

矩阵通过只说明已构造场景的离线路径；Discord/Kaiheila 的服务器事件不会自动进入子频道，昵称、权限、平台实际发送与双 bot HTTP 仍需联调。

最新快照：[消息与调用](baselines/2026-11-12-fixed-adapter_matrix.json)、[通知](baselines/2026-11-12-fixed-notice_matrix.json)；[能力边界](../docs/ADAPTER_SUPPORT.md)。

## 2026-11-13：工具动作协议

本地未发布实现，默认 legacy_json，tools 需显式启用。新增工具协议套件 39 个 unittest，在 Python 3.10 与 3.14 均通过。Python 3.10 默认 12/12 套件通过；Python 3.14 含矩阵 14/14 通过。运行副本同步后，新套件再次通过。

消息/调用矩阵 206 项、0 失败（新增 19 个适配器场景的工具 sender 文本/@ 发送）；通知 17 项中 15 OK、2 N/A、0 失败。没有真实模型、真实平台或双 bot HTTP 联调。

结果：[Python 3.10](baselines/2026-11-13-tools-py310.json)、[Python 3.14](baselines/2026-11-13-tools-py314.json)、[消息与调用](baselines/2026-11-13-tools-adapter_matrix.json)、[通知](baselines/2026-11-13-tools-notice_matrix.json)。协议使用与限制见 [TOOL_PROTOCOL.md](../docs/TOOL_PROTOCOL.md)。

## 同日后续：真实模型隔离冒烟

补充模型可见 schema 与字段校验回执，工具协议套件增至 40 个 unittest。Python 3.10 默认 12/12、Python 3.14 含矩阵 14/14 通过。三个真实模型基本场景分别通过，仍观察到偶发参数错误，不作为稳定性验收；没有真实平台发送或现有数据迁移。

[真实模型记录](../docs/LIVE_TOOL_SMOKE.md)、[全部脱敏尝试](baselines/2026-11-13-live-tool-smoke.json)、[Python 3.10](baselines/2026-11-13-smoke-py310.json)、[Python 3.14](baselines/2026-11-13-smoke-py314.json)。

## 同日后续：工具稳定性重复验证

工具协议套件增至 42 个 unittest。修复后 7 类真实模型隔离场景共 17 次全部通过；修复前同样样本 15/17。严格校验保留，schema 内联展开、数组错误反馈和失败查询指引已补齐；发送未知后增加跨轮、后台及同批 sender 重建的停止保护。详见 [重复样本](baselines/2026-11-13-tool-stability.json)。

最终 Python 3.10 默认 12/12 通过；Python 3.14 含矩阵 14/14 通过。消息/调用 206、通知 17 项矩阵通过。运行副本的工具协议与历史落盘两个套件再次通过；wheel 核对 34 个源码模块且不含测试或开发工具。

首次 Python 3.10 全量为 11/12：历史落盘套件 B2 出现一次发送未确认，确切原因未能复现；开启诊断的三次独立复跑均通过。随后补齐模拟 OneBot 的成功发送 message_id、登录信息和按请求用户返回的成员信息；不将这项 fixture 修正认定为已证明的原异常根因。更新 fixture 后完整 3.10、相关 3.14 及运行副本复测通过。保留初始失败摘要与后续记录：[回归快照](baselines/2026-11-13-stability-regression.json)。后续灰度继续观察发送未知状态。

本轮没有真实平台发送、现有数据迁移或机器人重启；小样本通过不代替日常周期灰度。

## 同日后续：模拟会话完整链路

按用户要求自行构造会话，真实模型两轮检查均通过；每轮含 12 个主流程和 3 个独立进程恢复检查。平台由本地 OneBot API 桩接管，消息走生产 handle_event、matcher、批处理和原生发送导出，没有替换 send_unimsg。覆盖双消息合批、交错会话、原生 @、禁用会话、测试迁移备份、原位记忆纠错、reset 和真实新进程恢复。

新增 v_simulated_session_test.py，离线模式在 Python 3.10 仓库源码、Python 3.14 运行副本均通过。当前默认套件数为 13，含适配器矩阵为 15；本轮仅重跑新增套件，不将此前全量 12/14 的结果改写为 13/15。业务包源码未改变，本体 34、peer 2 个 Python 文件与运行副本仍一致。

所有数据和备份属于独立模拟目录，未改真实配置、迁移真实记忆或重启机器人。详见 [模拟说明](../docs/SIMULATED_SESSION.md) 和 [脱敏快照](baselines/2026-11-13-simulated-session.json)。没有真实平台、peer HTTP、生图实服务或 24 小时灰度验收。

## 同日后续：真实驱动与外部模拟平台

Python 3.10 工作区虚拟环境实际运行 FastAPI/Uvicorn 与 OneBot 适配器，按项目清单加载本体运行副本、rollpig、echo 及依赖，共 9 个插件；执行启动和关闭生命周期。控制器位于另一进程，经真实反向 WebSocket 收发 OneBot 事件和 API 回执，未替换 Bot.call_api/send_unimsg 或直接调用 handle_event。

脚本化模型与真实模型分别通过 20 项检查，包括重新启动后的磁盘历史/事实恢复；两次服务进程正常退出。初期测试入口搜索路径和脚本化模型历史标记选择的失败一并保留：[脱敏快照](baselines/2026-11-13-connected-platform.json)。业务包源码未变，默认离线套件计数不变；该项是单独运行、需要绑定端口和工作区依赖的集成检查。

原 .env* 与真实数据未改；所有数据属于测试目录，服务只监听 127.0.0.1。详见 [启动说明与边界](../docs/CONNECTED_PLATFORM.md)。没有真实聊天服务、peer HTTP、搜索、生图或长时间运行验收。

## 同日后续：双 bot 与实服务

本机命令、peer 鉴权/白名单/HTTP 调用/结果回传、更新后的 DeepSeek 搜索和 MiniMax-H3-Image 实服务共 16 项通过。实际图片 2560×2560，经原生 OneBot 图片段抵达模拟平台；生成期间其它会话消息未改变目标。搜索独立验证返回 2 条有效 URL 的结果，OpenWebSearch 备用实服务亦返回 2 条。

发现并修复本体/peer 的查询 API 捕获重入风险、搜索失败被当作空结果成功的问题。工具协议 44 个 unittest；peer 事件复制/捕获共 4 个。Python 3.10 本体 13/13、peer 2/2；Python 3.14 本体含矩阵 15/15、peer 2/2。相关运行副本再次验证；两侧 wheel 精确包含本体 34、peer 2 个源码模块。

真实记忆的 6 个 JSON 只复制到私有测试目录后离线初始化：3 个会话、0 错误、3 份迁移备份，内容和重复初始化后的引用保留，原文件哈希不变。未将副本送入模型。

保留旧 API 的额度/模型错误、初次双实例失败以及观察器错误导致的超时，最终复测通过；[实服务快照](baselines/2026-11-13-live-services.json)、[范围与复跑](../docs/LIVE_SERVICES.md)。没有真实聊天服务或连续 24 小时运行证明。


## 2026-11-13：会话灰度、诊断、发布门槛与 peer 请求关联

本体 3.10 仓库源码 15/15 套件、3.14 运行副本含适配器矩阵 17/17；实际 FastAPI/外部模拟 OneBot 25 项通过，真实模型/本机与 peer HTTP 路由 12 项通过，含 request_id 回传。
最终源码构建的 wheel/sdist 均通过完整包内容核对，分别在全新 3.10 虚拟环境安装、pip check 和隔离目录插件加载通过。公开脱敏摘要：[2026-11-13-operations.json](baselines/2026-11-13-operations.json)。

GitHub Actions 仅完成工作流实现与本地 YAML 解析，未提交推送执行；没有发布、修改生产配置或重启现有机器人。真实聊天平台与长期灰度仍未验证。中途测试失败和修复日志保留在 .results；最终通过结果见上述摘要。

## 2026-11-14：远端 Linux CI 已通过

已提交并推送 beta；被测代码提交 `dd9983a` 的 [Quality gate](https://github.com/Funny1Potato/nonebot-plugin-aigf-master/actions/runs/43545288155) 为 completed/success。Ubuntu 远端回归：3.10 为 15/15 套件，3.14 含适配器矩阵为 17/17；Python 3.11 构建、包内完整源码核对、twine check，以及 wheel/sdist 各自独立干净环境安装、pip check 和安装包加载全部通过。

首轮任务通过但上传器默认忽略隐藏目录，未保留测试日志；现已设置 include-hidden-files=true 和 if-no-files-found=error，复跑确认 offline-3.10、offline-3.14、checked-distributions 三个产物均上传成功。精确提交、任务编号、日志摘录及产物摘要见 [2026-11-14-ci.json](baselines/2026-11-14-ci.json)。

后续仅更新文档和基线记录，未修改被测包内 Python、依赖或工作流；无需重复业务回归。未创建新版本/tag，未执行上传 PyPI 的发布任务；真实聊天平台和长期灰度仍未验证。

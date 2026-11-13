# aigf-manager 实际启动与外部平台连接验证

2026-11-13，已实际启动工作区测试实例。本轮采用独立进程模拟外部 OneBot 平台，经真实 TCP/WebSocket 连接机器人，不直接调用 handle_event，不替换 Bot.call_api 或 send_unimsg。

## 实际运行方式

- 使用 `D:/DSH-workspace/aigf-manager/.venv/Scripts/python.exe`，进程工作目录为 aigf-manager。
- 从工作区 `pyproject.toml` 加载插件；本体源码路径核对为工作区运行副本。同时加载 rollpig、echo 和其依赖，共 9 个插件。
- 启动 FastAPI 驱动的真实 ASGI 服务与 Uvicorn，执行 NoneBot 生命周期；本体启动钩子运行，启动两个会话的批处理任务。
- 控制器作为另一个进程中的外部平台，连接 `ws://127.0.0.1:<临时端口>/onebot/v11/ws`，发送原生 OneBot 事件，并通过 action/params/echo 接收和应答机器人 API 调用。
- 模型使用 `.env.dev` 中的模型及代理配置；真实模型模式不替换 LLM。离线模式保留同一网络链路，仅将模型响应换为脚本。

不是原配置的无人值守生产启动：本次显式将监听地址、OneBot token、启用会话、协议和数据路径覆盖为测试值；只导入模型传输配置，不导入真实群列表或 peer 配置。所有数据、缓存、角色配置和模拟数据备份位于独立测试目录，不更改 `.env*` 或原业务数据。测试协议为 tools，原环境默认协议保持 legacy_json。

rollpig 实际加载并执行其生命周期，资源同步在测试配置中关闭，PigHub 延迟刷新调整到一小时后，避免该无关功能在短测试期间拉取外部资源。没有修改其安装包文件。

## 结果

脚本化模型和真实模型两种模式各通过 20 项检查：

- 两次实际启动均加载项目插件、运行本体启动钩子和批处理循环。
- 缺失 token 的反向 WebSocket 连接被拒绝；授权连接注册真实 OneBot Bot。
- 启动时将模拟旧数字会话目录迁移为适配器前缀目录；工具初始化保留旧短期记忆备份。
- 两个消息经网络入站，模型提交长期记忆，随后通过网络回复，并保留原生 @。
- 交错会话的发送目标隔离，禁用会话不落入处理；长期记忆原位纠错且 entry_id 不变。
- 管理命令 reset 经网络进入真实 matcher，清空历史并保留已提交记忆，之后可以继续对话。
- 第一次服务关闭后启动新进程、重新连接外部模拟平台，聊天历史从磁盘恢复，真实模型答出已修改的颜色事实。
- 两个服务进程均正常返回，ASGI shutdown 执行，测试结束回收连接和临时监听服务。

观察到的警告只有既有 pyproject 旧格式提示、故意缺少鉴权的测试连接及测试结束的断连提示；最终真实模型运行没有 ERROR/CRITICAL 诊断。

初期启动脚本缺少工作区搜索路径时本体未加载，补齐路径后正确加载运行副本。另一次脚本化模型错误读取历史中的旧测试标记，导致恢复颜色断言失败，已修正为只读取本批消息。上述是测试入口和 fixture 问题，没有改动业务包源码；保留失败记录，不将其误记为模型或插件故障。

脱敏记录：[连接测试快照](../tests/baselines/2026-11-13-connected-platform.json)。

## 复跑

在 aigf-manager 根目录运行：

```powershell
.\.venv\Scripts\python.exe ..\nonebot-plugin-aigf-master\tools\run_connected_platform.py --workspace . --offline
.\.venv\Scripts\python.exe ..\nonebot-plugin-aigf-master\tools\run_connected_platform.py --workspace . --env-file .env.dev --live
```

live 会产生现有模型服务费用。两个模式都实际启动服务、执行项目插件启动钩子并绑定本地临时端口；不属于默认离线测试集，也不改变既有套件计数。入口必须明确指定 workspace 与 offline/live。

配置和临时 token 通过子进程 stdin 传入，不写入命令行或报告。诊断会屏蔽模型密钥、端点和代理值；输出保存在 `tests/.results/connected-platform-*`。健康/关闭接口只存在于本次测试入口中，要求临时 token，业务插件没有新增这些接口。

## 仍未验证

后续已完成本机插件调用、peer 双 bot HTTP、更新后的搜索及生图实服务验证，见 [LIVE_SERVICES.md](LIVE_SERVICES.md)。下段保留本页初次连接测试的范围。

外部平台是本机 OneBot 协议模拟器，尚未连接真实聊天服务，未验证平台登录、权限、网络抖动和限流。peer HTTP、搜索及生图实服务本次关闭；rollpig 仅验证加载与生命周期，未验证完整业务。未做真实数据迁移或连续 24 小时观察，没有提交、发布或改动现有机器人的配置。

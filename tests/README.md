# 离线验证

这些脚本从原本机临时目录归档而来，保留行为断言，仅调整加载路径、隔离目录、退出码与当前 mock 接口。没有启动机器人或连接真实平台。

## 安装与运行

Python >=3.10。在仓库根目录创建自己的虚拟环境，然后执行：

```powershell
python -m pip install -e . -r tests/requirements.txt
python tests/run_offline.py
```

本机可直接使用 `D:/DSH-workspace/aigf-manager/.venv/Scripts/python.exe`（3.10）或 `.venv314/Scripts/python.exe`（3.14）。这些环境可复用，不是仓库必须存在的目录。

- 默认加载当前仓库源码，每个脚本使用独立子进程。
- `--source <包的父目录>` 可加载另一份源码；入口使用绝对路径，工作目录不影响目标选择。
- `--suite v_ma_test.py` 选择单个脚本，可重复指定。默认每脚本超时 180 秒，用 `--timeout` 调整。
- 每次运行将 stdout/stderr 与退出码保存到 `tests/.results/<时间>/`，包括 summary.json；失败、超时或进程异常退出均返回非零。
- 各脚本使用唯一的 `tests/.runs/` 子目录，屏蔽继承的 AIGFM 配置并使用测试凭据；不加载工作区 `.env`，不写真实记忆、缓存或预设。输出留存便于排查，这两个目录均被 Git 忽略。
- 所有 API 业务行为使用 mock；这些是离线行为测试，不代表真实平台、真实模型或当前外部服务可用。
- 不要把全部脚本 import 到同一个 pytest 进程：NoneBot 注册表与事件循环属于进程状态。

## 套件

| 脚本 | 覆盖 |
| --- | --- |
| v_event_copy_test.py | Satori/Kaiheila 原生消息槽位、缓存、@ 段与目标响应器 |
| v_notice_fallback_test.py | 通知精确群/房间键、拒绝歧义字段 |
| v_tool_protocol_test.py | 全工具多轮、发送幂等、部分失败、记忆身份/版本/并发、收藏与学习、生图、迁移预览 |
| v_invocation_test.py | 共享调用额度、解析重试、固定身份、投递确定性与取消处理 |
| v_ma_test.py | 会话、消息渲染、捕获、调用、发送、迁移 |
| v_notice_test.py | 通知预筛、文案、会话定位与 handler |
| v_recent_persist_test.py | 聊天历史恢复、坏数据、reset |
| v_beta_port_test.py | 文本 @、多模态模式、MIME、图片 id 锚点 |
| v_parser_test.py | JSON 修复、响应解析、解析重试 |
| v_search_test.py | DeepSeek 请求、响应、异常处理 |
| v_anime_chain_test.py | 角色识别双后端、渲染和缓存 |
| v_cache_cleanup_test.py | 图片缓存清理 |
| v_ma_adapters_test.py | 可选：适配器消息/调用能力矩阵 |
| v_notice_adapters_test.py | 可选：适配器通知能力矩阵 |

## 适配器诊断

```powershell
python -m pip install -r tests/requirements-matrix.txt
python tests/run_offline.py --matrix
```

完整矩阵建议 Python >=3.12（本次使用 3.14），3.10 下部分适配器自身无法导入。
依赖文件记录本次环境中的适配器版本，插件基础依赖沿用 pyproject；不是完整锁文件。
矩阵中的 FAIL 仍返回非零，不能算作通过。历史 Satori/Kaiheila 调用与通知缺口已在本轮离线修复；真实平台能力仍以实际部署为准，当前结果见 BASELINE.md 和原始日志。
`v_notice_recipes.py` 仅作为通知事件构造配方使用，其历史分组名称不是当前支持声明。

未收录 `v_pr1_at_test.py`：它比较旧 PR 与 main 的临时源码快照，不是当前 beta 的回归用例。

本次验证结果：[BASELINE.md](BASELINE.md)。

工具协议使用脚本化 mock LLM 和模拟发送验证，不调用真实模型或聊天平台。历史落盘套件现在通过 run_batch 和真实发送入口验证确认后的聊天记录，不再将仅生成 reply 当作已发送。

真实模型检查单独显式运行，不属于离线套件，使用合成数据并捕获平台发送。入口、费用及已知限制见 [LIVE_TOOL_SMOKE.md](../docs/LIVE_TOOL_SMOKE.md)。

`v_simulated_session_test.py` 覆盖完整消息入口、批处理、原生 OneBot 发送、reset 和独立进程恢复，共 15 个检查；默认只运行离线模型。真实模型模拟入口见 [SIMULATED_SESSION.md](../docs/SIMULATED_SESSION.md)。

实际启动工作区驱动并由外部 OneBot 模拟器通过反向 WebSocket 连接的测试需单独运行，会绑定本地端口并加载项目插件；入口和结果见 [CONNECTED_PLATFORM.md](../docs/CONNECTED_PLATFORM.md)。不属于默认离线测试集。

双实例 peer HTTP、本机插件、搜索与后台生图实服务检查见 [LIVE_SERVICES.md](../docs/LIVE_SERVICES.md)，需显式 --live；默认离线套件不会调用这些付费服务。


## b5 后新增检查

`v_distribution_test.py` 验证发布门槛会拒绝 tag/版本不符、缺失源码和混入私有数据。质量工作流还会实际构建 wheel/sdist 并分别在干净环境安装、检查依赖和加载插件。Python 3.10 需要测试依赖中的 tomli；干净安装工具为 `python tools/check_clean_install.py --dist dist`，结果保存在 tests/.results/install-*。

`v_operations_test.py` 覆盖会话协议持久化、跨 await 切换边界、会话隔离、坏文件保留、取消清理、诊断内容边界、token 统计及 peer 早到/迟到/不匹配输出。实际 FastAPI/外部 OneBot 模拟检查见 `tools/run_connected_platform.py --offline`；实际模型及 peer HTTP 往返见 `tools/run_service_checks.py --live --checks routing`，这两个工具不纳入默认离线套件。

# 会话灰度、诊断与发布检查

本文描述 2.0.0b6 新增的会话灰度、诊断和主端 peer 请求关联能力。全局默认仍为 `legacy_json`；无需改生产配置即可保持现有行为。

## 按会话选择协议

在 `.env` 中设置初始覆盖（必须写完整会话键）：

```dotenv
AIGFM_ACTION_PROTOCOL=legacy_json
AIGFM_SESSION_PROTOCOLS={"onebot11:123456":"tools","satori:guild_channel":"legacy_json"}
AIGFM_PEER_OUTPUT_TIMEOUT=60
```

超级用户在目标会话执行以下命令。命令前缀沿用 NoneBot 的 `COMMAND_START`，例如配置 `/` 时使用 `/action_protocol tools`。

| 命令 | 行为 |
| --- | --- |
| `action_protocol tools` / `动作协议 tools` | 当前会话下一批开始使用显式工具 |
| `action_protocol legacy_json` | 当前会话下一批回退旧 JSON 协议 |
| `action_protocol default` | 删除管理员覆盖，恢复该会话环境配置；没有会话配置时恢复全局值 |
| `status` / `状态` | 当前批、下一批协议、运行诊断及 peer 请求状态 |

优先级：管理员覆盖 > `AIGFM_SESSION_PROTOCOLS` > `AIGFM_ACTION_PROTOCOL`。管理员覆盖原子保存到 localstore 插件配置目录的 `session_protocols.json`，重启保留；`reset` 不清除协议设置。损坏或未知版本的覆盖文件不会被自动重写。命令不会启用原本未启用的会话。

协议在批次开始时冻结，即使处理期间修改设置，当前批也继续原协议。不同会话独立，不修改共享的全局配置对象。`tools` 仍只执行工具，不发布普通结束文字或解析结束 JSON。

## 运行诊断

每会话保留最近 20 批的内存摘要，`status` 展示最近 5 批及窗口内近期工具失败；重启清空。摘要不保存对话正文、工具参数、返回内容或凭据。

- 显示实际协议、停止原因、整个批次耗时、模型轮次、tools 调用数、token、忽略的普通结束文本数。
- `turn_limit` / `call_limit` / `deadline` 表示轮次、调用数或截止预算耗尽；`request_error` / `request_timeout` / `parse_error` 等保留各自原因。
- 工具失败和不确定结果显示工具名与错误码。批次 `completed` 仅说明模型回合结束，仍需查看工具结果。
- token 只统计服务端实际报告值；没有报告时显示“未报告”。旧 JSON 模式也在实际 SDK 请求处累计轮次及 token，包含解析重试。
- 原有的最近消息展示仍保留，只允许超级用户查询；诊断窗口属于额外的无正文摘要。

## Peer 请求与输出关联

主实例生成的 `request_id` 同时用于本地台账、`/peer/invoke`、peer 回执及 `/peer/capture`。工具回执的 `data.request_id` 是插件投递编号；外层 `request_id` 仍是模型工具调用编号，两者用途不同。

peer 通过请求局部上下文关联输出，包含继承上下文的后台任务；不同请求/会话不共享编号，主动发到其它会话的输出不附带原编号。合成事件不会替换最近真实事件模板。主实例只接受已知编号且 peer、会话均匹配的关联；其它输出仍按原有捕获方式处理，不伪造关联。

`status` 展示当前会话最近 5 条请求；主实例最多保留 200 条请求摘要，重启清空：

| 状态 | 含义 |
| --- | --- |
| 投递中 | 等待 HTTP 回执 |
| 已接收，等待输出 | peer 返回 accepted，未据此确认执行 |
| 接收不确定 | 请求可能送达，不自动重试 |
| 收到输出 | 捕获推送已回到主实例；不代表完整执行或聊天平台发送成功 |
| 等待输出超时 | 接收/不确定之后超过配置期限仍没有关联输出，不重发；迟到输出可以更新为“收到输出” |
| 未投递 | 已知连接失败或明确拒绝 |

等待期限从 HTTP 回执到达起算，查询状态时计算是否超时；无需为每条请求启动定时任务。旧 peer 缺失编号时仍能调用和推送，但只能显示等待/超时，不能可靠关联；旧主实例发来的无编号请求也仍可执行。不要因没有关联输出就宣称插件失败或自动重发。

## 发布门槛

两个发布仓库分别有 `quality.yml`：PR、main/beta push、手动触发均可执行；`publish.yml` 在 tag 发布前调用同一质量工作流。

1. Python 3.10 / 3.14 分进程离线回归；3.14 安装完整适配器矩阵。
2. 构建 wheel 和 sdist，核对完整 Python 源码集合及内容、项目名称/版本，拒绝意外文件、测试或私有数据；tag 必须精确等于 `v` + pyproject 版本。
3. wheel、sdist 各在独立干净虚拟环境中安装，执行 `pip check`，在源码目录外以隔离 Python 加载已安装插件。
4. 全部通过才下载并上传核对过的同一组构建产物；不重新构建，不使用 `--skip-existing` 隐藏重复版本。

本地复核（在对应发布仓库执行）：

```console
python -m pip install build twine
python -m build
python tools/check_distribution.py --dist dist
python tools/check_distribution.py --dist dist --tag v2.0.0b6
python -m twine check dist/*
python tools/check_clean_install.py --dist dist
python tests/run_offline.py
```

peer 使用自己的版本/tag；Python 3.10 运行检查脚本需要 `tomli`（已在测试依赖声明）。`dist` 应是本次构建的干净目录。干净安装检查在 `tests/.results/install-*` 保留日志和独立环境，使用仓库既有代理，不读取生产 `.env`。

质量门槛不触发真实模型、搜索、生图或聊天平台调用。本地测试与远端 GitHub Actions 分别记录；远端运行结果见 [当前状态](CURRENT_STATE.md)，不要把本地通过当作远端通过。

## 本地验收（2026-11-13）

本体 3.10 仓库源码 15/15 套件、3.14 运行副本含矩阵 17/17；peer 两套环境各 4/4。实际 FastAPI/外部模拟 OneBot 25 项通过，包含管理员切换、旧协议回复、status、重启保留与恢复配置；真实模型驱动的双实例路由 12 项通过，包含 request_id 端到端关联。两个包的 wheel/sdist 均在独立全新 3.10 环境安装、检查依赖和加载通过。

记录见 [本体公开摘要](../tests/baselines/2026-11-13-operations.json) 及两个仓库的 tests/BASELINE.md。源码已同步运行副本；没有提交、推送、发布、修改生产配置或重启现有机器人。

## 远端验收与交付（2026-11-14）

两个仓库的 beta 均已提交推送。远端 Ubuntu 质量门槛全部通过：本体 Python 3.10 为 15/15、3.14 含矩阵为 17/17；peer 两环境各 4/4。构建及 wheel/sdist 分别在新 Python 3.11 环境安装、依赖检查和隔离加载也全部通过。已确认测试日志和构建产物成功上传。

被测提交和运行链接见 [当前状态](CURRENT_STATE.md)；上述 2026-11-13 小节为当时本地验收记录，不代表仍未提交或仍未运行远端 CI。当前仍未创建新发布，真实聊天平台与长期灰度尚未验收。

## 2.0.0b6 发布交付（2026-11-14）

本体 b6 已发布到 GitHub 预发布和 PyPI；tag 的发布工作流完成质量门槛并上传成功，实际下载包与 CI 构建产物及 tag 源码核对一致。main README 的 beta 提示已更新。上文远端验收小节描述发布前阶段，当前交付见 [项目状态](CURRENT_STATE.md) 和 [发布核验](releases/2.0.0b6-verification.json)。peer 尚未在本轮发布，完整关联需另行部署包含 request_id 支持的 peer。

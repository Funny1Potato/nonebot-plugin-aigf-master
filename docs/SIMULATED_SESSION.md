# 模拟会话端到端验证

2026-11-13。按用户要求自行构造会话，不连接真实聊天平台，不改运行配置或真实业务数据。

## 覆盖范围和结果

两轮真实模型模拟会话均通过 12 个主流程检查和 3 个新进程恢复检查。最终脚本也在 Python 3.10 仓库源码、Python 3.14 运行副本上通过离线套件。

| 检查 | 验证内容 |
| --- | --- |
| 消息入口与合批 | 通过 NoneBot handle_event、生产 matcher、uninfo/uniseg 解析及真实批处理循环接收两个消息；禁用会话不进入缓冲 |
| 发送身份与目标 | A 会话模型处理中插入 B 会话消息，原生 OneBot @ 仍指向 A 的用户，两边回复各归自己的群 |
| 存储与迁移备份 | 旧短期记忆初始化前的备份字节一致；长期记忆成功提交；另一会话没有被写入 |
| 记忆纠错 | 原位替换颜色事实，条目 entry_id 不变，未重复新增 |
| 新进程恢复 | 停止父进程批处理任务后，启动独立 Python 进程；初始历史缓存为空，从同一测试数据目录恢复事实和聊天历史，再由模型通过工具回答当前颜色 |
| reset | 使用真实管理命令 matcher 清空内存和磁盘聊天历史；已提交长期记忆保留，后续对话不重新导入旧历史 |

模型侧使用现有配置，平台侧使用真实 OneBot 事件和消息类及本地 API 桩。发送仍经过生产 MessageSender、uniseg 导出、Bot.send/call_api，桩返回唯一 message_id；没有替换最终 send_unimsg 函数。离线模式只替换模型响应，不会调用模型服务。

两个模拟会话均启用 tools，不涉及生产环境按会话切换协议。默认运行配置仍为 legacy_json。

## 复跑

离线模式无需工作区配置：

```powershell
python tools/simulate_tool_session.py --offline
python tests/run_offline.py --suite v_simulated_session_test.py
```

显式调用现有模型（会产生模型服务费用），在运行工作区执行：

```powershell
.\.venv\Scripts\python.exe ..\nonebot-plugin-aigf-master\tools\simulate_tool_session.py --live --workspace . --env-file .env.dev
```

`--source` 可指定被测包的父目录，以验证运行副本。必须明确选择 offline/live；不指定时直接退出，不创建模拟数据目录。

每次运行写入独立 `tests/.results/simulated-session-*`。模拟数据备份和迁移备份留在该目录，恢复子进程只能使用此处已有的模拟目录。它通过 stdin 接收最小模型配置，不将密钥写入命令行、配置文件或报告；退出或超时会回收子进程。报告只保留布尔检查、逐轮工具名称、服务商提供的 token 计数和源码哈希，不保存模型回复正文。最终版同时校验父子进程使用相同测试脚本。

脱敏快照：[模拟会话验证记录](../tests/baselines/2026-11-13-simulated-session.json)。

## 尚未覆盖

后续已补充实际启动 aigf-manager 并由外部进程经反向 WebSocket 连接的验证，见 [CONNECTED_PLATFORM.md](CONNECTED_PLATFORM.md)。本页保留的是较早的进程内平台桩验证。

这验证的是模拟平台下的完整会话流程，未运行 NoneBot 驱动及生产启动钩子，不代表真实平台登录、网络、权限或限流通过；未联调 peer HTTP、搜索或生图实服务，也没有连续 24 小时观察。真实数据未迁移或备份，后续实际启用仍需安排备份和平台灰度。

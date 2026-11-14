# 工具动作协议：使用与维护

2026-11-13，首版随 2.0.0b5 发布；文末的 b5 后开发补充尚未发布。开发方案见 [TOOL_ONLY_AGENT_PLAN.md](TOOL_ONLY_AGENT_PLAN.md)。

## 启用

当前默认仍为 `legacy_json`，以便先做真实模型与会话灰度；不会因更新源码而自动改变运行协议。

在机器人环境配置中选择：

```env
AIGFM_ACTION_PROTOCOL=tools
AIGFM_LLM_MAX_TOOL_TURNS=3
AIGFM_TOOL_MAX_CALLS=12
AIGFM_TOOL_BATCH_TIMEOUT=180
AIGFM_TOOL_SEND_MAX_MESSAGES=3
AIGFM_TOOL_MEMORY_ENABLED=true
```

配置按正常 NoneBot 启动流程加载。本次开发没有改 `.env*` 或启动/重启机器人。

- tools：发言、记忆、收藏和学习走工具；普通结束文本不发布，旧最终 JSON 不执行。
- legacy_json：保留旧 JSON 协议。实际回复发送改为共用 sender，只在发送得到确认后记录真实发言。
- 不自动探测并切换协议；模型不支持工具时本批停止，管理命令 status/状态可查看协议及最近停止原因。
- tools 下旧 `AIGFM_LLM_JSON_MODE`、`AIGFM_LLM_TOOLS_JSON_STRICT` 不控制请求，不发送最终 JSON 格式要求。
- 关闭记忆工具只移除查询/修改入口，已有记忆仍以只读上下文提供；系统昵称/群名片更新保留。

## 迁移前预览

源码部署可先运行只读预览（参数为插件 data_dir，而不是它下面的 memory 目录）：

```powershell
python tools/preview_tool_migration.py --data-dir <插件数据目录>
```

输出只有文件数量、待初始化元信息数、待分命名空间数、归属不明记录数与坏文件数，不输出成员 id 或记忆正文，也不写任何文件。

工具模式首次使用相关记忆集合时：

1. 在业务字段旁新增 `_aigfm_meta.collections`，记录 entry_id、revision、内容哈希；不把字符串列表改成对象列表。
2. 改动前生成一次 `.pre-tools.bak`，并记录会话级 `tool_migration.json`。
3. 保留可确认属于 OneBot V11 的数字好友文件路径；其它适配器使用 `friends/<adapter>/<用户标识哈希>.json`。
4. 归属混合的旧记录不自动归给某个适配器，原文件保留；新记录使用明确身份的命名空间。
5. 坏的记忆文件不覆盖，相关工具返回不可用，普通发送及其它正常集合仍可以使用。

工具引用在当前集合 revision 下有效。旧模式或手工编辑改变内容时，会更新 revision；必要时重建该集合条目引用。模式切换不能继续执行切换前未完成的工具上下文。

回退到旧协议只在新批次开始时生效；已发送消息、已提交记忆不回滚，也不自动重放。若回退旧二进制版本，新的非 OneBot 命名空间需要兼容导出/映射，不应假设旧代码能读全。

## 工具契约

| 工具 | 范围与确认 |
| --- | --- |
| send_message | messages 数组，每条有 key 和 text/at/meme 段；固定本批会话，逐条报告状态 |
| query_memory | short_term、long_term、friend_info、friend_aliases、culture；返回条目 id 和 revision |
| update_memory | 一个目标集合的 add/modify/delete；改删必须带 entry_id，检查 expected_revision 后原子提交 |
| save_meme | 当前会话仍有效的 cache_id；返回保存、已存在、缺失或失败 |
| query_commands | 指定 current/peer、source/name 过滤，包含低置信度记录 |
| manage_command | learn/edit/request_delete；本批证据引用、明确来源、保存完成后才确认 |
| search_internet | 沿用原搜索客户端，回执限制体积 |
| invoke_plugin / invoke_peer_plugin | 只允许当前清单中的命令；保留白名单、自家命令拒绝、去重与能量限制 |
| generate_image | accepted + job_id，后台生成后使用同一 sender；预留发送名额，不等于已经发图 |

关闭某功能不会回到相应 JSON 字段。工具数量与可用命令按配置动态注入。参数采用 Pydantic 2 严格校验，不对坏参数做猜测性修补。模型可见 schema 内联展开引用，类型错误回执提供具体字段和数组格式提示。

### 发送例子

```json
{
  "messages": [
    {"key": "reply_1", "parts": [
      {"type": "at", "target": "10001"},
      {"type": "text", "content": "记住了。"}
    ]}
  ]
}
```

必须先获得实际记忆提交成功回执，才能在下一轮确认“记住了”。同一模型响应发生失败/冲突/不确定后，后续预先安排的发送暂不执行，要求模型根据回执重新决定。

发送前校验全部请求：无效 @、缺失表情包、长度/数量超限都在首发前拒绝。@ 不再静默丢弃。图片可能拆开前后文本，额度按拼装后的发送单元计。

- 每条逻辑消息最多 20 段、文本合计 2000 字符。
- key 在本批内幂等；同 key 改内容会冲突；不同 key 的同内容也在本批去重。
- 发送结果为 sent、partial、not_sent 或 uncertain，附逐条状态。
- 网络发出后超时或取消不能当作确定未发送；本批停止后续发送，包括换 key/内容与后台图片；同批 sender 重建也保留停止状态，不整体重试。
- 消息历史和基础能量在实际发送后结算，同批基础能量只计一次；表情包使用次数不在预检时增加。
- 适配器报告成功表示发送调用完成，不保证已读或平台后续一直展示。

## 预算与生命周期

- 默认 3 轮；每轮最多执行 4 个调用，整批默认最多 12 个调用，错误/拒绝/重复调用也计入预算。
- 默认整批 180 秒；单次模型请求最多 60 秒，工具还受剩余时间约束。
- 到上限即停止，不再要求最终 JSON 或自动重跑整批；模型不调工具时可以合法沉默。
- 命令尝试额度沿用 `AIGFM_INVOKE_MAX_PER_BATCH`，跨所有轮次共享。
- 发送尝试默认 3 个，包含生图任务预留；记忆每调用最多 20 个操作、每批最多 40 个实际改动。
- 后台生图独立最长 300 秒，相同参数的在途任务不重复创建；新消息不替换它的 bot/session。
- reset 使旧任务失去发布资格；不能撤销已经在途的网络发送，但其结果不会作为重置后的新发言写回。

## 持久化效果记录

发送前写 intent，取得回执后写状态，保存在 `memory/tool_effects/<batch_id>.json`。键名和内容使用不同指纹空间，恢复时不会因相同 key 或相同内容重复发送。

- 文件只保存会话、编号、摘要哈希和状态，不复制整份群聊正文。
- 重启后的无结果 intent、未完成 accepted 任务视为不确定，只恢复诊断，不自动补发或重新生图。
- 工具模式首次恢复会话时，将本会话未确定效果作为系统状态补进上下文；不会放进别的会话。
- reset 保存时间标记，避免旧效果诊断重新灌回已重置的上下文；原效果证据仍保留。
- 完成记录默认保留 7 天；未确定记录不随普通清理静默删除。
- 没有平台幂等接口时不承诺端到端 exactly-once，只保证本程序不主动重放确认或未知效果。

## 实现边界

- 协调锁是单进程内共享锁；记忆暂用同一数据根的粗粒度锁以保护旧代码的读改写，命令库和表情包各自协调。不要多进程共写同一数据目录。
- JSON 小文件采用 fsync + 同目录原子替换。首次初始化大量文件会增加本地 IO；迁移预览和备份用于评估这部分变化。
- 查询结果有分页和体积限制，过大的旧条目返回预览，不修改原文。
- 新模式的轮次、工具数、停止原因、耗时以及服务商实际提供的 token 用量进入 ProcessResult；不猜测缺失的用量数据。
- 已进行隔离真实模型冒烟检查，补充修复后 7 类场景 17 次通过，保留修复前错误记录；见 [验证记录](LIVE_TOOL_SMOKE.md)。后续已完成真实模型、双实例 peer HTTP、搜索与生图实服务联调，见 [实服务记录](LIVE_SERVICES.md)；尚未连接真实聊天平台或完成 24 小时灰度，不能视为长期稳定性验收。

验证结果与日志入口见 [tests/BASELINE.md](../tests/BASELINE.md)。

完整入口到发送的真实模型模拟会话、迁移备份与独立进程恢复验证见 [SIMULATED_SESSION.md](SIMULATED_SESSION.md)；该模拟没有连接真实聊天平台。

实际工作区启动、生命周期及外部反向 WebSocket 连接验证见 [CONNECTED_PLATFORM.md](CONNECTED_PLATFORM.md)，仍使用模拟聊天服务。

本机插件、peer HTTP、搜索和后台生图的实服务验证见 [LIVE_SERVICES.md](LIVE_SERVICES.md)。搜索异常使用 SEARCH_HTTP_ERROR/SEARCH_SERVICE_ERROR 失败回执，正常空结果不属于服务失败。

## b5 后 beta 源码补充

新增按会话协议覆盖、管理员 `action_protocol` 命令、`status` 诊断及 peer 请求输出关联，均已在 beta 源码实现，尚未发布到 PyPI。配置优先级、下一批生效规则与兼容边界见 [运维说明](OPERATIONS.md)。全局默认仍为 legacy_json。

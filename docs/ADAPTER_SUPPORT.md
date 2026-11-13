# 适配器能力与验证边界

2026-11-13，本地未发布源码验证。没有连接真实聊天平台、真实 LLM 或运行双 bot HTTP 联调。

## 本轮实际覆盖

| 路径 | 结果 | 含义 |
| --- | --- | --- |
| 消息/调用矩阵 | 206 个检查点全部通过 | 含复制后消息可读、原事件不变、19 个适配器场景的目标响应器命中及工具 sender 文本/@ 发送 |
| 通知矩阵 | 17 项中 15 OK、2 N/A | 含类型闸门、预筛、会话定位、无 JSON dump 的文案、handler 入缓冲 |
| Satori / Kaiheila 复制回归 | 本体与 peer 各 3 个 unittest 通过 | 原始/重复命令、@ 与文本段、缓存、原事件不变、实际 alconna matcher 命中 |
| 通知回落回归 | 3 个 unittest 通过 | 精确群/房间键、缺失/错误 id 拒绝、uninfo 优先 |

矩阵中部分 API 返回 mock 或主动失败，因此昵称查询、权限获取、捕获后的外部推送、平台实际发送等仍需真实部署验证。通过不代表每个能力在所有平台都具有相同语义。

## 通知定位策略

先用 uninfo，再用 alconna Target；两者都不能定位时，仅为以下明确字段提供回落：

| 适配器 | 通知字段 | 会话键 | 边界 |
| --- | --- | --- | --- |
| Feishu | event.chat_id（兼容嵌套 event.event.chat_id） | feishu:<chat_id> | 必须显式带聊天 id，不从用户资料猜群 |
| Kritor | group_id | kritor:<group_id> | 不以目标用户/操作者 id 代替群号 |
| bilibili Live | room_id | bilibililive:<room_id> | 不依赖该通知具有用户主体 |

回落只用于通知入缓冲，不创建发送目标或真实用户事件。启用清单仍须匹配，通知仍不覆盖调用身份，批处理仍等待真实用户消息。

- Discord / Kaiheila 的服务器成员事件可能只有 guild 信息：它属于服务器会话，不会自动进入 guild_channel 的频道历史，也不会向多个频道广播。频道事件只进入对应频道。
- Minecraft / WXMP 的本次通知配方没有群会话字段，按预期跳过；Console / Mail 没有通知事件类型。
- bilibili Live / YunHu 的完整矩阵仍需合适的 Python 版本，本次用 Python 3.14；默认回归同时在 3.10 跑测。

## 本轮修复与测试纠正

- Satori 的 `_message` 是构造期缓存，存在 pydantic extras 中；复制时刷新缓存并保留 message.id，而非仅删懒加载缓存。
- Kaiheila 的 message 是只读属性，实际读取 event.content；替换真正的 Message 容器，保留 @ 等原生段。
- 两侧插件修复相同复制逻辑，保持独立包，未引入互相依赖。
- 矩阵探针改为强引用单个 Alconna 命令；mock 的动态 API 尊重预设返回值，保留同步辅助方法，补全必要配置与频道字段。
- Discord / EFChat 原来的通知失败由 mock 缺字段导致；另外三项确实需要本体补通知会话回落。

历史失败快照保留在 tests/baselines；最新快照使用 `2026-11-12-fixed-*.json`，不覆盖历史证据。

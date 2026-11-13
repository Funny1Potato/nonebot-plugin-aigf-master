# 开发、同步与发布

## 工作顺序

1. 在目标仓库检查 `git status --short --branch`、版本和现有差异。本体与 peer 分开处理，不自动切分支。
2. 默认以目标发布仓库作为修改源；运行副本用于联调。若先改运行副本，先检查仓库侧是否有独立修改，明确同步方向。
3. 使用指定虚拟环境的 Python 跑仓库 `tests/run_offline.py`。测试默认加载仓库源码；`--source` 可指定包的父目录，核对运行副本。
4. 仅同步包内 `.py`，比较完整文件名集合和忽略行尾后的内容。新增文件要同步，删除文件逐个确认并清理；不要直接递归镜像目录。
5. 核对两侧一致，再按任务授权决定是否提交、推送或发版。需要真实平台联调时单独安排重启与验证。

## 本机路径

| 包 | 发布仓库包目录 | 运行副本 |
| --- | --- | --- |
| 本体 | D:/DSH-workspace/nonebot-plugin-aigf-master/nonebot_plugin_aigf_master | D:/DSH-workspace/aigf-manager/aigf_manager/plugins/nonebot_plugin_aigf_master |
| peer | D:/DSH-workspace/nonebot-plugin-aigfm-peer/nonebot_plugin_aigfm_peer | D:/DSH-workspace/aigf-manager/nonebot_plugin_aigfm_peer |

同步不包含 README、pyproject、tests、`.env*`、缓存和业务数据。测试和开发文档留在发布仓库；运行项目只保留导航和运行配置。本地 .venv 与 .venv314 可复用，不依赖 PATH 中的 nb/python。

工作区只读同步检查：`python tools/check_source_sync.py --master-repo ../nonebot-plugin-aigf-master --peer-repo ../nonebot-plugin-aigfm-peer`（在 aigf-manager 根目录执行）。有缺失或内容差异时返回非零；忽略 CRLF/LF。

## 发布约定

- main 与 beta 独立维护；本体与 peer 各自版本、各自发布。按最终代码选择分支，不能用复制 README 代替功能合并。
- 修改 pyproject 版本后再打相应 tag；beta 使用预发布号，Release 标为 prerelease。不能仅凭 README 或本地版本断言 PyPI 已发布。
- 长说明写入文件，`gh release create ... --notes-file <文件>`；发完核对 release body 非空、Actions 和 PyPI 实际结果。
- 沿用本机仓库已有代理，不绕过代理；不要将凭据或 `.env*` 纳入 Git。
- 打包后确认只包含插件包；tests/docs 不应进入安装包。已增加明确的包发现范围。

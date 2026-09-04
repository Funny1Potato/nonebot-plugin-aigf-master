"""插件发现：扫描已注册的命令型 Matcher"""

from nonebot.matcher import matchers
from nonebot.rule import CommandRule

from .models import PluginCommand


def discover_commands() -> list[PluginCommand]:
    """扫描所有已注册的 Matcher，提取命令型匹配器"""
    commands: list[PluginCommand] = []
    seen = set()

    for priority, matcher_list in matchers.items():
        for matcher_cls in matcher_list:
            cmd_info = _extract_command(matcher_cls)
            if cmd_info and cmd_info.name not in seen:
                seen.add(cmd_info.name)
                commands.append(cmd_info)

    return commands


def build_command_plugin_map() -> dict[str, str]:
    """全量扫描 → {主命令名/别名: plugin_name}，供调用核对（不受白名单影响）"""
    mapping: dict[str, str] = {}
    for cmd in discover_commands():
        mapping[cmd.name] = cmd.plugin_name
        for alias in cmd.aliases:
            mapping[alias] = cmd.plugin_name
    return mapping


def _extract_command(matcher_cls) -> PluginCommand | None:
    """从 Matcher 中提取命令信息"""
    try:
        rule = matcher_cls.rule
        if not rule or not rule.checkers:
            return None

        for checker in rule.checkers:
            if isinstance(checker.call, CommandRule):
                cmd_rule = checker.call
                if not cmd_rule.cmds:
                    return None

                # 取第一个命令作为主命令
                main_cmd = cmd_rule.cmds[0]
                cmd_name = main_cmd[0] if main_cmd else ""
                if not cmd_name:
                    return None

                # 收集别名
                aliases = []
                for cmd_tuple in cmd_rule.cmds[1:]:
                    if cmd_tuple:
                        aliases.append(cmd_tuple[0])

                # 获取插件名和描述
                plugin_name = matcher_cls.plugin_name or "unknown"
                description = ""
                if matcher_cls.plugin and hasattr(matcher_cls.plugin, "metadata"):
                    meta = matcher_cls.plugin.metadata
                    if meta:
                        description = meta.description or ""

                return PluginCommand(
                    name=cmd_name, aliases=aliases,
                    plugin_name=plugin_name, description=description,
                )
    except Exception:
        pass
    return None

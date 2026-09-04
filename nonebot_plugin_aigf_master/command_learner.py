"""命令学习器：通过 LLM 学习命令，支持编辑和删除"""

import json
from datetime import datetime
from pathlib import Path

import anyio
from nonebot import logger

from .models import PluginCommand


class CommandLearner:
    """通过 LLM 学习命令，支持编辑和删除"""

    def __init__(self, data_dir: Path, min_confidence: int = 3):
        self._data_dir = data_dir
        self._min_confidence = min_confidence

        self._commands_file = data_dir / "learned_commands.json"
        self._peer_commands_file = data_dir / "peer_commands.json"

        self._commands: dict[str, dict] = {}
        self._peer_commands: dict[str, dict] = {}

        self._save_tasks: set = set()

    async def load(self):
        for label, target, f in [
            ("当前实例", self._commands, self._commands_file),
            ("其它bot", self._peer_commands, self._peer_commands_file),
        ]:
            if not f.exists():
                continue
            try:
                async with await anyio.open_file(f, encoding="utf-8") as fp:
                    data = json.loads(await fp.read())
                for cmd in data.get("commands", []):
                    target[cmd["name"]] = cmd
                logger.info(f"[命令学习] 已加载 {len(target)} 个{label}命令")
            except Exception as e:
                logger.error(f"[命令学习] 加载{label}命令失败: {e}")

    async def _save(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        for target, f in [(self._commands, self._commands_file), (self._peer_commands, self._peer_commands_file)]:
            data = {"commands": list(target.values())}
            async with await anyio.open_file(f, "w", encoding="utf-8") as fp:
                await fp.write(json.dumps(data, ensure_ascii=False, indent=2))

    def _schedule_save(self):
        """立即异步保存到文件"""
        import asyncio
        task = asyncio.create_task(self._save())
        self._save_tasks.add(task)
        task.add_done_callback(self._save_tasks.discard)

    def learn_command(self, name: str, parameters: str, usage: str, hook_type: str, source: str,
                      examples: list[str] | None = None):
        """学习一个命令，立即保存"""
        target = self._commands if hook_type == "current" else self._peer_commands

        if name in target:
            target[name]["confidence"] += 1
            if parameters:
                target[name]["parameters"] = parameters
            if usage:
                target[name]["usage"] = usage
            if examples:
                existing = target[name].setdefault("examples", [])
                for ex in examples:
                    if ex not in existing and len(existing) < 10:
                        existing.append(ex)
        else:
            target[name] = {
                "name": name,
                "plugin": source,
                "hook_type": hook_type,
                "parameters": parameters,
                "usage": usage,
                "examples": examples or [],
                "confidence": 1,
                "first_seen": datetime.now().strftime("%Y-%m-%d"),
            }

        conf = target[name]["confidence"]
        logger.info(f"[命令学习] {hook_type} 命令: {name} (来源: {source}, 置信度: {conf})")
        self._schedule_save()

    def edit_command(self, name: str, parameters: str | None = None, usage: str | None = None,
                     examples: list[str] | None = None):
        """编辑命令，立即保存"""
        target = self._commands if name in self._commands else self._peer_commands
        if name not in target:
            return False
        if parameters is not None:
            target[name]["parameters"] = parameters
        if usage is not None:
            target[name]["usage"] = usage
        if examples is not None:
            target[name]["examples"] = examples
        logger.info(f"[命令学习] 编辑命令: {name}")
        self._schedule_save()
        return True

    def delete_command(self, name: str) -> dict:
        """删除命令（带置信度判断）"""
        target = self._commands if name in self._commands else self._peer_commands
        if name not in target:
            return {"deleted": False, "name": name, "delete_confidence": 0}

        cmd = target[name]
        cmd["delete_confidence"] = cmd.get("delete_confidence", 0) + 1
        del_conf = cmd["delete_confidence"]

        if del_conf >= self._min_confidence:
            del target[name]
            logger.info(f"[命令学习] 删除命令: {name} (达到阈值 {del_conf})")
            self._schedule_save()
            return {"deleted": True, "name": name, "delete_confidence": del_conf}

        logger.info(f"[命令学习] 删除命令待确认: {name} (置信度: {del_conf}/{self._min_confidence})")
        self._schedule_save()
        return {"deleted": False, "name": name, "delete_confidence": del_conf}

    @staticmethod
    def _format_cmd(cmd: dict) -> str:
        """格式化命令展示文本（可选参数 + 使用方法 + 示例，不含命令名本身）"""
        parts = []
        if cmd.get("parameters"):
            parts.append(f"参数: {cmd['parameters']}")
        if cmd.get("usage"):
            parts.append(f"用法: {cmd['usage']}")
        if cmd.get("examples"):
            parts.append(f"示例: {' | '.join(cmd['examples'][:3])}")
        return " - ".join(parts)

    def get_all_commands(self) -> list[PluginCommand]:
        """获取所有学习到的命令（含与静态命令重名的，由调用方合并覆盖）"""
        result = []
        for cmd in self._commands.values():
            if cmd.get("confidence", 0) < self._min_confidence:
                continue
            result.append(PluginCommand(
                name=cmd["name"],
                aliases=[],
                plugin_name=cmd.get("plugin", ""),
                description=self._format_cmd(cmd),
            ))
        return result

    def get_peer_commands(self) -> list[dict]:
        """获取置信度足够的其它 bot 命令"""
        result = []
        for cmd in self._peer_commands.values():
            if cmd.get("confidence", 0) < self._min_confidence:
                continue
            result.append({
                "name": cmd["name"],
                "bot": cmd.get("plugin", "unknown"),
                "description": self._format_cmd(cmd),
                "confidence": cmd["confidence"],
            })
        return result

    def find_command_plugin(self, name: str) -> str | None:
        """查学习命令归属插件名（查 _commands 与 _peer_commands），用于调用核对"""
        for store in (self._commands, self._peer_commands):
            cmd = store.get(name)
            if cmd:
                return cmd.get("plugin") or None
        return None

"""角色预设存储"""

import json
from dataclasses import asdict
from pathlib import Path

import anyio
from nonebot import logger

from .models import RolePreset

_DEFAULT_PRESET = RolePreset(
    name="小助手", role="一个友好的群聊助手，会用轻松的语气和大家聊天",
)


class PresetStore:
    """角色预设存储"""

    def __init__(self, config_dir: Path):
        self._dir = config_dir / "presets"
        self._presets: dict[str, RolePreset] = {}

    async def load_all(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        default_file = self._dir / "default.json"
        if not default_file.exists():
            async with await anyio.open_file(default_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(asdict(_DEFAULT_PRESET), ensure_ascii=False, indent=2))
        try:
            for filename in self._dir.iterdir():
                if filename.suffix == ".json":
                    try:
                        async with await anyio.open_file(filename, encoding="utf-8-sig") as f:
                            data = json.loads(await f.read())
                        self._presets[filename.stem] = RolePreset(**data)
                    except Exception as e:
                        logger.warning(f"无法加载预设 {filename.name}: {e}")
        except Exception as e:
            logger.error(f"扫描预设目录失败: {e}")
        logger.info(f"[启动] 预设加载完成: {len(self._presets)} 个")

    def get(self, name: str) -> RolePreset | None:
        return self._presets.get(name)

    def list_all(self) -> dict[str, RolePreset]:
        return dict(self._presets)

    async def save(self, name: str, preset: RolePreset):
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{name}.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(asdict(preset), ensure_ascii=False, indent=2))
        self._presets[name] = preset

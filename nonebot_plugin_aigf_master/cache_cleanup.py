"""缓存目录清理：文件数超过上限时按 mtime 最旧删除

覆盖两处只增不减的缓存：
- `image_cache/`：VLM 描述缓存与角色识别结果缓存（JSON）
- `raw/`：按 fileid 命名的原始图片缓存（单文件较大）
约定与 sticker_cache 一致：按文件 mtime 从旧到新删（不区分最近是否用过），删掉只是下次重新识别/重新下载。
"""

from pathlib import Path

from nonebot import logger


def prune_dir(directory: Path, limit: int, pattern: str = "*") -> int:
    """把目录内文件数压到 limit 以内（limit<=0 表示不限制），返回删除数量"""
    if limit <= 0 or not directory.exists():
        return 0
    try:
        files = [p for p in directory.glob(pattern) if p.is_file()]
        if len(files) <= limit:
            return 0
        files.sort(key=lambda p: p.stat().st_mtime)
    except OSError:
        return 0

    removed = 0
    for old in files[: len(files) - limit]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info(f"[清理] {directory.name} 缓存清理: {removed} 个（上限 {limit}）")
    return removed

"""记忆存储管理

负责短期记忆、长期记忆、群友信息、文化记忆的持久化。
"""

import json
from datetime import datetime
from pathlib import Path

import anyio
from nonebot import logger

from .models import MemoryOps


class MemoryStore:
    """每群独立的记忆存储"""

    def __init__(self, group_id: str, data_dir: Path):
        self.group_id = group_id
        self._group_dir = data_dir / "memory" / group_id
        self._group_dir.mkdir(parents=True, exist_ok=True)
        self._friends_dir = data_dir / "memory" / "friends"
        self._friends_dir.mkdir(parents=True, exist_ok=True)

    # ========== 短期记忆 ==========

    async def load_short_term(self) -> list[str]:
        path = self._group_dir / "short_term.json"
        if not path.exists():
            return []
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read()).get("items", [])
        except Exception as e:
            logger.error(f"加载短期记忆失败: {e}")
            return []

    async def save_short_term(self, items: list[str]):
        path = self._group_dir / "short_term.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"items": items}, ensure_ascii=False, indent=2))

    # ========== 长期记忆 ==========

    async def load_long_term(self) -> list[str]:
        path = self._group_dir / "long_term.json"
        if not path.exists():
            return []
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read()).get("facts", [])
        except Exception as e:
            logger.error(f"加载长期记忆失败: {e}")
            return []

    async def save_long_term(self, facts: list[str]):
        path = self._group_dir / "long_term.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"facts": facts}, ensure_ascii=False, indent=2))

    # ========== 群友信息 ==========

    async def load_friend(self, user_id: str) -> dict | None:
        path = self._friends_dir / f"{user_id}.json"
        if not path.exists():
            return None
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read())
        except Exception as e:
            logger.error(f"加载群友 {user_id} 信息失败: {e}")
            return None

    async def save_friend(self, user_id: str, data: dict):
        path = self._friends_dir / f"{user_id}.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))

    async def load_group_friends(self) -> dict[str, dict]:
        """加载当前群所有有记录的群友"""
        result = {}
        if not self._friends_dir.exists():
            return result
        for path in self._friends_dir.glob("*.json"):
            try:
                async with await anyio.open_file(path, encoding="utf-8") as f:
                    data = json.loads(await f.read())
                if data.get("id") and self.group_id in data.get("groups", []):
                    result[data["id"]] = data
            except Exception:
                continue
        return result

    async def update_nickname(self, user_id: str, nickname: str):
        """自动更新群友的 QQ 全局昵称"""
        if not user_id or not nickname:
            return
        friend = await self.load_friend(user_id)
        if friend is None:
            friend = {
                "id": user_id, "nickname": nickname,
                "aliases": [], "past_nicknames": [],
                "info": [], "groups": [self.group_id],
            }
        else:
            old_nick = friend.get("nickname", "")
            if old_nick and old_nick != nickname:
                past = friend.get("past_nicknames", [])
                if old_nick not in past:
                    past.append(old_nick)
                friend["past_nicknames"] = past
            friend["nickname"] = nickname
            if self.group_id not in friend.get("groups", []):
                friend.setdefault("groups", []).append(self.group_id)
        await self.save_friend(user_id, friend)

    # ========== 文化记忆 ==========

    async def load_culture(self) -> list[dict]:
        path = self._group_dir / "culture.json"
        if not path.exists():
            return []
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read()).get("terms", [])
        except Exception as e:
            logger.error(f"加载文化记忆失败: {e}")
            return []

    async def save_culture(self, terms: list[dict]):
        path = self._group_dir / "culture.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"terms": terms}, ensure_ascii=False, indent=2))

    # ========== 操作执行 ==========

    async def apply_ops(self, ops: MemoryOps):
        """执行记忆操作」

        每个字段独立执行：单字段格式异常只影响该字段（记录日志），
        不中断其它字段的操作，避免 LLM 一次格式偏差吞掉整批记忆操作。
        """
        if not ops:
            return

        if ops.short_term:
            try:
                await self._apply_list_ops(
                    self.load_short_term, self.save_short_term, ops.short_term
                )
            except Exception as e:
                logger.error(f"[记忆] 短期记忆操作失败: {e}")
        if ops.long_term:
            try:
                await self._apply_list_ops(
                    self.load_long_term, self.save_long_term, ops.long_term
                )
            except Exception as e:
                logger.error(f"[记忆] 长期记忆操作失败: {e}")
        if ops.friends:
            try:
                await self._apply_friends_ops(ops.friends)
            except Exception as e:
                logger.error(f"[记忆] 群友信息操作失败: {e}")
        if ops.culture:
            try:
                await self._apply_culture_ops(ops.culture)
            except Exception as e:
                logger.error(f"[记忆] 文化记忆操作失败: {e}")

    @staticmethod
    def _ops_entry_list(ops, key: str, default: list = None) -> list:
        """取操作字段并保证返回 list：字符串转单元素，其它非 list 忽略

        LLM 输出的 memory 结构偶有偏差（add/delete/modify 为字符串或缺失），
        这里统一归一化，避免 `for x in "字符串"` 逐字符 / AttributeError。
        """
        if isinstance(ops, dict):
            val = ops.get(key, default if default is not None else [])
        else:
            val = default if default is not None else []
        if isinstance(val, list):
            return val
        if isinstance(val, str) and val:
            return [val]
        return []

    async def _apply_list_ops(self, loader, saver, ops):
        items = await loader()
        # LLM 可能输出裸数组（把展示结构当输出结构）→ 视为 add
        if not isinstance(ops, dict):
            if isinstance(ops, list):
                ops = {"add": ops}
            else:
                logger.warning(f"[记忆] 忽略非法的列表记忆操作: {ops!r}")
                return
        for idx in sorted((int(i) for i in self._ops_entry_list(ops, "delete")
                           if isinstance(i, (int, str)) and str(i).lstrip("-").isdigit()), reverse=True):
            if 0 <= idx < len(items):
                items.pop(idx)
        for mod in self._ops_entry_list(ops, "modify"):
            if not isinstance(mod, dict):
                continue
            try:
                idx, content = int(mod.get("index", -1)), mod.get("content", "")
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(items) and content:
                items[idx] = content
        for item in self._ops_entry_list(ops, "add"):
            if isinstance(item, str) and item and item not in items:
                items.append(item)
        await saver(items)

    async def _apply_friends_ops(self, friends_ops):
        if not isinstance(friends_ops, dict):
            logger.warning(f"[记忆] 忽略非法的群友操作: {friends_ops!r}")
            return
        nickname_to_id: dict[str, str] = {}
        for f in self._friends_dir.iterdir():
            if f.suffix == ".json":
                try:
                    async with await anyio.open_file(f, encoding="utf-8") as fh:
                        data = json.loads(await fh.read())
                    if data.get("nickname") and data.get("id"):
                        nickname_to_id[data["nickname"]] = data["id"]
                except Exception:
                    pass

        for key, ops in friends_ops.items():
            if not isinstance(key, str):
                continue
            # 单个群友的操作也允许裸数组（视为 add）
            if not isinstance(ops, dict):
                if isinstance(ops, list):
                    ops = {"add": ops}
                else:
                    continue
            user_id = key if key.isdigit() else nickname_to_id.get(key, key)
            friend = await self.load_friend(user_id) or {
                "id": user_id, "nickname": key if not key.isdigit() else "",
                "aliases": [], "past_nicknames": [], "info": [], "groups": [],
            }
            if self.group_id not in friend.get("groups", []):
                friend.setdefault("groups", []).append(self.group_id)

            info = friend.get("info", [])
            for idx in sorted((int(i) for i in self._ops_entry_list(ops, "delete")
                               if isinstance(i, (int, str)) and str(i).lstrip("-").isdigit()), reverse=True):
                if 0 <= idx < len(info):
                    info.pop(idx)
            for mod in self._ops_entry_list(ops, "modify"):
                if not isinstance(mod, dict):
                    continue
                try:
                    idx, content = int(mod.get("index", -1)), mod.get("content", "")
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(info) and content:
                    info[idx] = content
            for item in self._ops_entry_list(ops, "add"):
                if isinstance(item, str) and item and item not in info:
                    info.append(item)
            friend["info"] = info

            aliases = friend.get("aliases", [])
            for item in self._ops_entry_list(ops, "add_alias"):
                if isinstance(item, str) and item and item not in aliases:
                    aliases.append(item)
            for item in self._ops_entry_list(ops, "remove_alias"):
                if item in aliases:
                    aliases.remove(item)
            friend["aliases"] = aliases

            await self.save_friend(user_id, friend)

    async def _apply_culture_ops(self, ops):
        # LLM 可能输出裸数组（视为 add）
        if not isinstance(ops, dict):
            if isinstance(ops, list):
                ops = {"add": ops}
            else:
                logger.warning(f"[记忆] 忽略非法的文化记忆操作: {ops!r}")
                return
        terms = await self.load_culture()
        for idx in sorted((int(i) for i in self._ops_entry_list(ops, "delete")
                           if isinstance(i, (int, str)) and str(i).lstrip("-").isdigit()), reverse=True):
            if 0 <= idx < len(terms):
                terms.pop(idx)
        for mod in self._ops_entry_list(ops, "modify"):
            if not isinstance(mod, dict):
                continue
            try:
                idx = int(mod.get("index", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(terms):
                for key in ["term", "meaning", "context", "usage_examples"]:
                    if key in mod and mod[key]:
                        terms[idx][key] = mod[key]
        for item in self._ops_entry_list(ops, "add"):
            if isinstance(item, dict) and item.get("term"):
                existing = next((t for t in terms if t.get("term") == item["term"]), None)
                if existing:
                    existing["usage_count"] = existing.get("usage_count", 0) + 1
                else:
                    new_term = {
                        "term": item["term"],
                        "meaning": item.get("meaning", ""),
                        "context": item.get("context", ""),
                        "usage_examples": item.get("usage_examples", []),
                        "first_seen": datetime.now().strftime("%Y-%m-%d"),
                        "usage_count": 1,
                        "source": item.get("source", "learned"),
                    }
                    terms.append(new_term)
        await self.save_culture(terms)

    @staticmethod
    def match_culture(terms: list[dict], text: str) -> list[dict]:
        matched = []
        text_lower = text.lower()
        for term in terms:
            term_str = term.get("term", "").lower()
            if term_str and term_str in text_lower:
                matched.append(term)
        return matched

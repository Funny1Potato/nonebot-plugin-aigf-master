"""One sender for tool actions and accepted background image jobs."""
import asyncio
import base64
import json
from datetime import datetime
from pathlib import Path

import httpx
from nonebot import logger
from nonebot.exception import ActionFailed
from nonebot_plugin_alconna import UniMessage

from .effect_store import EffectStore
from .models import ChatMessage
from .session import resolve_at_target
from .store_coordinator import coordinated, fingerprint
from .tool_runtime import receipt
from .unimsg import compose_unimsg_list, send_unimsg


class MessageSender:
    def __init__(self, processor, context, legacy=False):
        self.legacy = legacy
        self.processor, self.context = processor, context
        self.store = EffectStore(processor.memory._group_dir.parent / "tool_effects", context.batch_id, processor.session_key)
        self.keys, self.content = {}, {}
        recovered_attempts = 0
        for key, effect in self.store.data["effects"].items():
            if effect.get("kind") == "send":
                status = self.store.get(key)["status"]
                if status == "uncertain":
                    self.context.send_halted = True
                if status in ("sent", "uncertain"):
                    self.content[effect["digest"]] = {"status": status, "key": effect.get("message_key", key)}
                if not effect.get("background"):
                    recovered_attempts += 1
            elif effect.get("kind") == "job":
                recovered_attempts += 1
        self.context.sends = max(self.context.sends, recovered_attempts)
        self.lock = asyncio.Lock()

    @property
    def limit(self):
        return 10000 if self.legacy else self.processor.config.aigfm_tool_send_max_messages

    def reserve_image(self):
        if self.context.send_halted or self.context.sends >= self.limit:
            return False
        self.context.sends += 1
        return True

    async def send_legacy(self, segments):
        from .tool_schemas import Message, Part, SendMessage
        # Keep the old public response shape; only the actual sending is shared.
        parts = []
        for segment in segments:
            if segment.type == "text":
                parts.append(Part.model_construct(type="text", content=segment.content))
            elif segment.type == "at":
                parts.append(Part.model_construct(type="at", target=segment.user_name))
            elif segment.type == "meme":
                parts.append(Part.model_construct(type="meme", id=segment.meme_id))
        args = SendMessage.model_construct(messages=[Message.model_construct(key="legacy_reply", parts=parts)])
        return await self.send(args)

    async def send(self, args):
        async with self.lock:
            if not self.context.current:
                return receipt("not_sent", "SESSION_RESET")
            prepared, replay, seen_keys = [], [], set()
            for message in args.messages:
                digest = fingerprint(message.model_dump(mode="json"))
                if message.key in seen_keys:
                    return receipt("not_sent", "DUPLICATE_MESSAGE_KEY")
                seen_keys.add(message.key)
                durable = self.store.get("logical:" + fingerprint(message.key))
                if durable and durable["digest"] != digest:
                    return receipt("not_sent", "MESSAGE_KEY_CONFLICT")
                if message.key in self.keys:
                    old_digest, result = self.keys[message.key]
                    if old_digest != digest:
                        return receipt("not_sent", "MESSAGE_KEY_CONFLICT")
                    replay.extend(result)
                    continue
                parts, labels, memes = [], [], []
                for part in message.parts:
                    if part.type == "text":
                        parts.append({"type": "text", "content": part.content})
                        labels.append(part.content.strip())
                    elif part.type == "at":
                        target = await resolve_at_target(self.context.invocation.bot,
                                                         self.context.invocation.session, part.target)
                        if not target and self.legacy:
                            continue
                        if not target:
                            return receipt("not_sent", "AT_TARGET_UNRESOLVED", {"target": part.target})
                        parts.append({"type": "at", "target": int(target) if str(target).isdigit() else target})
                        labels.append("@" + str(target))
                    else:
                        meme = self.processor.memes.all_memes.get(part.id)
                        if self.legacy and meme is None:
                            continue
                        if not self.processor.config.aigfm_meme_enabled or meme is None:
                            return receipt("not_sent", "MEME_NOT_FOUND")
                        path = self.processor.memes._data_dir / meme.filename
                        if not path.is_file():
                            return receipt("not_sent", "MEME_FILE_MISSING")
                        if part.id in self.processor.memes._recent and self.legacy:
                            continue
                        if part.id in self.processor.memes._recent:
                            return receipt("not_sent", "MEME_RECENTLY_USED")
                        parts.append({"type": "image", "path": str(path)})
                        labels.append(f"[表情包: {part.id}]")
                        memes.append((str(path), part.id))
                units, skipped = await compose_unimsg_list(self.context.invocation.bot,
                                                           self.context.invocation.session, parts)
                if skipped or not units:
                    return receipt("not_sent", "MESSAGE_CANNOT_BE_COMPOSED")
                for index, unit in enumerate(units):
                    text = unit.extract_plain_text()
                    meme_id = next((mid for path, mid in memes
                                    if any(str(getattr(segment, "path", "")) == path for segment in unit)), None)
                    visible = " ".join(getattr(segment, "text", "") if segment.type == "text"
                                       else "@" + segment.target if segment.type == "at"
                                       else f"[表情包: {meme_id}]" for segment in unit)
                    prepared.append({"key": message.key, "request_digest": digest,
                                     "effect": f"send:{fingerprint(message.key)}:{index}", "digest": fingerprint(json.dumps(unit.dump(media_save_dir=False), sort_keys=True, default=str)),
                                     "message": unit, "text": visible, "meme": meme_id})
            # All parameter checks precede any externally visible effect.
            pending_digests = {unit["digest"] for unit in prepared} - self.content.keys()
            needed = len(pending_digests)
            if needed and self.context.send_halted:
                return receipt("not_sent", "SEND_HALTED_AFTER_UNCERTAIN")
            if self.context.sends + needed > self.limit:
                return receipt("not_sent", "SEND_BUDGET_EXCEEDED")
            for unit in prepared:
                logical = "logical:" + fingerprint(unit["key"])
                if not self.store.get(logical):
                    self.store.record(logical, "prepared", unit["request_digest"], kind="logical")
            self.context.sends += needed
            results, stopped, released, attempted = [], False, set(), set()
            for unit in prepared:
                if unit["digest"] in self.content:
                    result = {**self.content[unit["digest"]], "key": unit["key"], "replayed": True}
                elif stopped:
                    if unit["digest"] not in released and unit["digest"] not in attempted:
                        self.context.sends -= 1
                        released.add(unit["digest"])
                    result = {"status": "not_attempted", "key": unit["key"]}
                else:
                    attempted.add(unit["digest"])
                    result = await self._unit(unit)
                    if result["status"] in ("sent", "uncertain"):
                        self.content[unit["digest"]] = result
                    stopped = result["status"] != "sent"
                results.append(result)
            for key in seen_keys:
                units = [u for u in prepared if u["key"] == key]
                if units:
                    self.keys[key] = (units[0]["request_digest"], [r for u, r in zip(prepared, results) if u["key"] == key])
            all_results = replay + results
            statuses = {r["status"] for r in all_results}
            status = "sent" if statuses == {"sent"} else "partial" if "sent" in statuses else "uncertain" if "uncertain" in statuses else "not_sent"
            return receipt(status, "MESSAGE_DELIVERY", {"messages": all_results})

    async def _unit(self, unit, background=False):
        if not self.context.current:
            return {"status": "not_sent", "key": unit["key"], "code": "SESSION_RESET"}
        previous = self.store.get(unit["effect"])
        if previous:
            if previous["digest"] != unit["digest"]:
                return {"status": "not_sent", "key": unit["key"], "code": "MESSAGE_KEY_CONFLICT"}
            if previous["status"] == "uncertain":
                self.context.send_halted = True
            return {"status": previous["status"], "key": unit["key"], "replayed": True}
        if self.context.send_halted:
            return {"status": "not_sent", "key": unit["key"], "code": "SEND_HALTED_AFTER_UNCERTAIN"}
        if unit.get("meme"):
            async with coordinated(self.processor.memes._data_dir):
                if unit["meme"] in self.processor.memes._recent:
                    return {"status": "not_sent", "key": unit["key"], "code": "MEME_RECENTLY_USED"}
                self.processor.memes._recent.append(unit["meme"])
        try:
            self.store.record(unit["effect"], "intent", unit["digest"], kind="send", message_key=unit["key"], background=background)
        except BaseException:
            if unit.get("meme") and unit["meme"] in self.processor.memes._recent:
                self.processor.memes._recent.remove(unit["meme"])
            raise
        invocation = self.context.invocation
        target = getattr(invocation.session, "target", None) or invocation.event
        status = "uncertain"
        try:
            if target is None or invocation.bot is None:
                status = "not_sent"
            else:
                timeout = 20 if background else min(20, self.context.remaining)
                if timeout <= 0:
                    status = "not_sent"
                else:
                    await asyncio.wait_for(send_unimsg(invocation.bot, target, unit["message"]), timeout=timeout)
                    status = "sent"
        except asyncio.CancelledError:
            self.context.send_halted = True
            self.store.record(unit["effect"], "uncertain", unit["digest"], kind="send", message_key=unit["key"], background=background)
            await self._uncertain_note(unit)
            raise
        except (ActionFailed, httpx.ConnectError, httpx.ConnectTimeout):
            status = "not_sent"
        except Exception as error:
            logger.warning(f"[发送] batch={self.context.batch_id} uncertain={type(error).__name__}")
        # A failed receipt write leaves a durable intent, never a replayable send.
        try:
            self.store.record(unit["effect"], status, unit["digest"], kind="send", message_key=unit["key"], background=background)
        except Exception:
            status = "uncertain"
        if self.context.current and status in ("sent", "uncertain"):
            chars = len(unit["message"].extract_plain_text())
            before = (0.03 if self.context.energy_base_paid else 0) + min(0.12, self.context.energy_characters * 0.002)
            self.context.energy_base_paid = True
            self.context.energy_characters += chars
            after = 0.03 + min(0.12, self.context.energy_characters * 0.002)
            self.processor.social_energy = max(0.0, self.processor.social_energy - (after - before))
        if status == "uncertain":
            self.context.send_halted = True
            await self._uncertain_note(unit)
        if status == "sent" and self.context.current:
            self.processor.recent_messages.append(ChatMessage(time=datetime.now(), user_name=self.processor.bot_name,
                                                             content=unit["text"]))
            self.processor.recent_messages[:] = self.processor.recent_messages[-50:]
            await self.processor._persist_recent()
        if unit.get("meme") and status == "not_sent" and unit["meme"] in self.processor.memes._recent:
            self.processor.memes._recent.remove(unit["meme"])
        if unit.get("meme") and status in ("sent", "uncertain"):
            async with coordinated(self.processor.memes._data_dir):
                mid = unit["meme"]
                if mid not in self.processor.memes._recent:
                    self.processor.memes._recent.append(mid)
                self.processor.memes._recent[:] = self.processor.memes._recent[-self.processor.memes._recent_max:]
                if status == "sent":
                    self.processor.memes.all_memes[mid].usage_count += 1
                    try:
                        await self.processor.memes.persist()
                    except OSError:
                        logger.warning("[表情包] 已发送，使用计数保存失败")
        return {"status": status, "key": unit["key"]}

    async def _uncertain_note(self, unit):
        if not self.context.current:
            return
        self.processor.recent_messages.append(ChatMessage(
            time=datetime.now(), user_name="系统",
            content=f"发送结果未确认（批次 {self.context.batch_id}，消息 {unit['key']}）：{unit['text'][:80]}。不要自动重发，也不要断言未发送。"))
        self.processor.recent_messages[:] = self.processor.recent_messages[-50:]
        await self.processor._persist_recent()

    async def send_image(self, job_id, b64):
        async with self.lock:
            unit = {"key": job_id, "effect": "asset:" + job_id, "digest": fingerprint(b64),
                    "message": UniMessage.image(raw=base64.b64decode(b64, validate=True)), "text": "[AI 生成图片]", "meme": None}
            return await self._unit(unit, background=True)

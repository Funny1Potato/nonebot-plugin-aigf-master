"""Tool adapters over stores, the existing command guards and the shared sender."""
import asyncio
from copy import deepcopy
from datetime import datetime
from .models import ChatMessage
from pathlib import Path
from uuid import uuid4

from .message_sender import MessageSender
from .store_coordinator import coordinated, fingerprint
from .tool_memory import ToolMemory
from .tool_runtime import receipt
from .tool_schemas import schema


class ToolHandlers:
    def __init__(self, processor, context, commands, peer_commands, legacy_handler, known_owners):
        self.processor, self.context = processor, context
        self.commands, self.peer_commands = commands, peer_commands
        self.legacy_handler = legacy_handler
        self.memory = ToolMemory(processor.memory, context, known_owners)
        self.sender = MessageSender(processor, context)
        context.sender = self.sender
        self.observations = set()

    def tools(self):
        config = self.processor.config
        names = ["send_message"]
        if config.aigfm_tool_memory_enabled:
            names += ["query_memory", "update_memory"]
        if config.aigfm_meme_enabled:
            names.append("save_meme")
        if config.aigfm_learn_commands:
            names += ["query_commands", "manage_command"]
        if config.aigfm_search_enabled and self.processor.search:
            names.append("search_internet")
        if config.aigfm_invoke_enabled and self.commands:
            names.append("invoke_plugin")
        if config.aigfm_invoke_enabled and self.processor.peer_client and self.peer_commands:
            names.append("invoke_peer_plugin")
        if config.aigfm_image_gen_enabled and self.processor.image_gen:
            names.append("generate_image")
        return [schema(name) for name in names]

    async def __call__(self, name, args):
        proc, ctx = self.processor, self.context
        if name == "send_message":
            return await self.sender.send(args)
        if name == "query_memory":
            return await self.memory.query(args)
        if name == "update_memory":
            return await self.memory.update(args)
        if name == "query_commands":
            return await proc.learner.query_tools(args)
        if name == "manage_command":
            from .processor import SELF_COMMANDS, SELF_PLUGIN_NAMES, _command_head
            if _command_head(args.name) in SELF_COMMANDS or args.source in SELF_PLUGIN_NAMES:
                return receipt("failed", "SELF_COMMAND_FORBIDDEN")
            evidence = [ctx.evidence.get(ref) for ref in args.evidence_refs]
            if any(item is None for item in evidence) or not any(args.name in item.content for item in evidence):
                return receipt("failed", "NEW_MESSAGE_EVIDENCE_REQUIRED")
            if args.route == "peer":
                if not proc.peer_client or args.source not in proc.peer_client.names():
                    return receipt("failed", "UNKNOWN_COMMAND_SOURCE")
            else:
                from .plugin_discovery import discover_commands
                from nonebot import get_loaded_plugins
                sources = {c.plugin_name for c in discover_commands()} | {plugin.name for plugin in get_loaded_plugins()}
                if args.source not in sources:
                    return receipt("failed", "UNKNOWN_COMMAND_SOURCE")
            key = (args.action, args.route, args.source, args.name)
            if args.action in ("learn", "request_delete") and key in self.observations:
                return receipt("unchanged", "OBSERVATION_ALREADY_COUNTED")
            result = await proc.learner.manage_tools(args)
            if result["ok"]:
                self.observations.add(key)
            return result
        if name == "save_meme":
            async with coordinated(proc.memes._data_dir):
                info = proc.memes.get_cache_info(proc.session_key, args.cache_id)
                if not info or not Path(info.get("path", "")).is_file():
                    return receipt("failed", "MEME_CACHE_MISSING")
                if args.cache_id in proc.memes.all_memes:
                    return receipt("already_exists", "MEME_ALREADY_EXISTS", {"meme_id": args.cache_id})
                before = deepcopy(proc.memes._collected_memes)
                try:
                    saved = await proc.memes.save_from_cache(proc.session_key, args.cache_id,
                                                             args.description, args.keywords)
                except Exception:
                    proc.memes._collected_memes = before
                    raise
                if saved:
                    if args.cache_id not in proc.memes.all_memes:
                        return receipt("unchanged", "MEME_NOT_RETAINED")
                    return receipt("saved", "MEME_SAVED", {"meme_id": args.cache_id})
                existing = next((m.id for m in proc.memes.all_memes.values() if args.cache_id in m.filename), None)
                if existing:
                    return receipt("already_exists", "MEME_ALREADY_EXISTS", {"meme_id": existing})
                return receipt("failed", "MEME_NOT_SAVED")
        if name == "generate_image":
            if args.reference_cache_id:
                info = proc.memes.get_cache_info(proc.session_key, args.reference_cache_id)
                if not info or not Path(info.get("path", "")).is_file():
                    return receipt("not_sent", "REFERENCE_IMAGE_NOT_IN_SESSION")
            result = start_image_job(proc, ctx, self.sender, args)
            await proc._persist_recent()
            return result
        if name in ("invoke_plugin", "invoke_peer_plugin"):
            if name == "invoke_plugin" and args.command not in {c.name for c in self.commands}:
                return receipt("not_sent", "COMMAND_NOT_IN_LIST")
            if name == "invoke_peer_plugin" and not any(c["name"] == args.command and c["bot"] == args.bot for c in self.peer_commands):
                return receipt("not_sent", "COMMAND_NOT_IN_LIST")
            ctx.invocation.last_delivery = None
            payload = args.model_dump(exclude_none=True)
            message = await self.legacy_handler(name, payload)
            result = ctx.invocation.last_delivery
            if result is None:
                return receipt("not_sent", "COMMAND_REJECTED", {"message": message})
            return receipt(result.status, "COMMAND_DELIVERY", {"message": message})
        if name == "search_internet":
            results = await proc.search.search(args.query, proc.config.aigfm_search_max_results)
            error = getattr(results, "error", None)
            if error:
                return receipt("failed", error["code"], {"http_status": error.get("http_status"),
                    "hint": "搜索服务失败，没有本次搜索证据；检查服务配置、权限或配额，不要声称已搜到或无相关结果。"})
            return receipt("ok", "SEARCH_RESULTS", {"text": proc.search.format_results(results)[:10000]})
        return receipt("failed", "UNKNOWN_TOOL")


def start_image_job(proc, ctx, sender, args):
    ctx.sender = sender
    digest = fingerprint(args.model_dump())
    active = getattr(proc, "_active_image_requests", {})
    proc._active_image_requests = active
    previous = active.get(digest)
    if previous and previous[1] == ctx.generation:
        return receipt("accepted", "IMAGE_ALREADY_ACCEPTED", {"job_id": previous[0]})
    if not sender.reserve_image():
        return receipt("not_sent", "SEND_HALTED_AFTER_UNCERTAIN" if ctx.send_halted else "SEND_BUDGET_EXCEEDED")
    job = "image_" + uuid4().hex
    sender.store.record("job:" + job, "accepted", digest, kind="job")
    ctx.generation_jobs[job] = "accepted"
    active[digest] = (job, ctx.generation)
    proc.recent_messages.append(ChatMessage(datetime.now(), "系统",
        f"已接受生图任务 {job}：{args.prompt[:120]}，尚未完成，不要重复提交。"))

    async def run_job():
        try:
            await asyncio.wait_for(proc._generate_image_bg(args.prompt, args.size,
                ctx.invocation.user_id, args.reference_cache_id, context=ctx, job_id=job), timeout=300)
        except asyncio.TimeoutError:
            ctx.generation_jobs[job] = "uncertain"
        except asyncio.CancelledError:
            ctx.generation_jobs[job] = "cancelled"
            raise
        except Exception:
            ctx.generation_jobs[job] = "failed"
        finally:
            if active.get(digest, (None,))[0] == job:
                active.pop(digest, None)
            sender.store.record("job:" + job, ctx.generation_jobs[job], digest, kind="job")

    task = asyncio.create_task(run_job())
    proc._image_gen_tasks.add(task)
    def finished(task):
        proc._image_gen_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error:
                from nonebot import logger
                logger.warning(f"[生图] 后台任务记录失败: {type(error).__name__}")
    task.add_done_callback(finished)
    return receipt("accepted", "IMAGE_JOB_ACCEPTED", {"job_id": job})

"""Isolate legacy offline checks from developer configuration and runtime data."""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("AIGFM_TEST_SOURCE", REPO)).resolve()
if not SOURCE.is_dir():
    raise RuntimeError(f"Test source does not exist: {SOURCE}")
# Each script owns a separate process and directory; no real .env is loaded.
for key in list(os.environ):
    if key.upper().startswith("AIGFM_"):
        del os.environ[key]
runs = REPO / "tests" / ".runs"
runs.mkdir(exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix=Path(sys.argv[0]).stem + "-", dir=runs))
os.chdir(TMP)
sys.path.insert(0, str(SOURCE))
os.environ["AIGFM_LLM_API_KEY"] = "test-key"
os.environ["AIGFM_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["AIGFM_VLM_ENABLED"] = "false"
os.environ["AIGFM_ANIME_BACKEND"] = "off"
os.environ["LOCALSTORE_USE_CWD"] = "true"


def close_loop(loop):
    """Register explicit shutdown for legacy scripts owning an asyncio loop."""
    import asyncio
    import atexit

    def cleanup():
        if loop.is_closed():
            return
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()

    atexit.register(cleanup)

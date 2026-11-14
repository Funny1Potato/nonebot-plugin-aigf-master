"""Run with a clean venv Python; import the installed plugin outside the checkout."""
import argparse
import importlib.metadata
from pathlib import Path
import os
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution")
    parser.add_argument("version")
    args = parser.parse_args()
    for key in list(os.environ):
        if key.upper().startswith("AIGFM_"):
            del os.environ[key]
    os.environ["AIGFM_LLM_API_KEY"] = "installation-check-key"
    os.environ["AIGFM_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
    package = args.distribution.replace("-", "_")
    assert importlib.metadata.version(args.distribution) == args.version
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="aigfm-install-") as directory:
        try:
            os.chdir(directory)
            import nonebot
            nonebot.init(driver="~none", localstore_use_cwd=True, log_level="CRITICAL")
            plugin = nonebot.load_plugin(package)
            assert plugin is not None, "Installed plugin failed to load"
            location = Path(plugin.module.__file__).resolve()
            assert "site-packages" in location.parts, f"Import came from checkout: {location}"
            print(f"PASS: clean installed {args.distribution} {args.version}")
        finally:
            os.chdir(original)


if __name__ == "__main__":
    main()

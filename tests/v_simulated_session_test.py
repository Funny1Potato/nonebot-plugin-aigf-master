"""Full native OneBot ingress, batching, send, reset and fresh-process recovery."""
from _bootstrap import REPO, SOURCE

import json
from pathlib import Path
import subprocess
import sys


result = subprocess.run([sys.executable, str(REPO / "tools/simulate_tool_session.py"),
    "--offline", "--source", str(SOURCE)], capture_output=True, text=True,
    encoding="utf-8", errors="replace", timeout=120)
print(result.stdout)
if result.returncode:
    print(result.stderr)
    raise SystemExit(result.returncode)
path = next(line.removeprefix("Report: ").strip()
            for line in result.stdout.splitlines() if line.startswith("Report: "))
report = json.loads(Path(path).read_text(encoding="utf-8"))
recovery = json.loads((Path(path).parent / "resume-report.json").read_text(encoding="utf-8"))
assert report["passed"] and len(report["checks"]) == 12
assert recovery["passed"] and recovery["fresh_process"] and len(recovery["checks"]) == 3
assert not report["live_model"] and not report["real_platform"]
assert report["source_sha256"] == recovery["source_sha256"]
assert report["harness_sha256"] == recovery["harness_sha256"]
print("PASS: 15 entry/batch/native-send/migration/reset/recovery checks")

"""Run isolated legacy suites; return nonzero on failure or timeout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", action="store_true", help="Include optional adapter diagnostics")
    parser.add_argument("--suite", action="append", help="Script filename; repeat to select suites")
    parser.add_argument("--source", type=Path, help="Parent directory of the package under test")
    parser.add_argument("--timeout", type=float, default=180, help="Seconds per suite")
    args = parser.parse_args()
    tests = Path(__file__).resolve().parent
    names = sorted(p.name for p in tests.glob("v_*test.py")
                   if args.matrix or "adapters" not in p.name)
    if args.suite:
        unknown = set(args.suite) - set(p.name for p in tests.glob("v_*test.py"))
        if unknown:
            parser.error(f"Unknown suites: {sorted(unknown)}")
        names = args.suite
    if not names:
        parser.error("No suites selected")
    output = tests / ".results" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if args.source:
        env["AIGFM_TEST_SOURCE"] = str(args.source.resolve())
    results = []
    for name in names:
        log = output / (name + ".log")
        with log.open("w", encoding="utf-8") as stream:
            try:
                proc = subprocess.run([sys.executable, str(tests / name)], cwd=tests,
                                      env=env, stdout=stream, stderr=subprocess.STDOUT,
                                      timeout=args.timeout)
                status = "PASS" if proc.returncode == 0 else "FAIL"
                code = proc.returncode
            except subprocess.TimeoutExpired:
                status, code = "TIMEOUT", None
        print(f"{status}: {name} ({log})", flush=True)
        results.append({"suite": name, "status": status, "exit_code": code, "log": str(log)})
    (output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sum(r['status'] == 'PASS' for r in results)}/{len(results)} suites passed")
    return int(any(r["status"] != "PASS" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())

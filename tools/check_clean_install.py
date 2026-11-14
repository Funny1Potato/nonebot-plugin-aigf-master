"""Install each local distribution in its own fresh venv, check deps and load it."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    output = root / "tests/.results"
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="install-", dir=output))
    temp = run / "temp"
    temp.mkdir()
    os.environ.update(TEMP=str(temp), TMP=str(temp))
    tempfile.tempdir = str(temp)
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        env.pop(key, None)
    proxy = subprocess.run(["git", "-C", str(root), "config", "--get", "http.proxy"], capture_output=True, text=True).stdout.strip()
    if proxy:
        env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy)
    results = []
    for kind in ("whl", "tar.gz"):
        artifacts = list(args.dist.resolve().glob("*." + kind))
        if len(artifacts) != 1:
            raise ValueError("Expected exactly one artifact per type")
        target = run / ("wheel" if kind == "whl" else "sdist")
        log_path = run / (kind + ".log")
        status = "failed"
        with log_path.open("w", encoding="utf-8") as log:
            try:
                venv.EnvBuilder(with_pip=True).create(target)
                python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                for command in ([str(python), "-m", "pip", "install", "--disable-pip-version-check", str(artifacts[0])],
                                [str(python), "-m", "pip", "check"],
                                [str(python), "-I", str(root / "tools/check_installed.py"), project["name"], project["version"]]):
                    subprocess.run(command, cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
                status = "passed"
            except subprocess.CalledProcessError as error:
                if error.output:
                    log.write(error.output.decode("utf-8", errors="replace") if isinstance(error.output, bytes) else error.output)
        results.append({"artifact": artifacts[0].name, "status": status})
        print(f"{status}: clean {kind} install ({log_path})", flush=True)
    (run / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return int(any(row["status"] != "passed" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())

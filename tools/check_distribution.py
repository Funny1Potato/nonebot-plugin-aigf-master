"""Fail closed on tag/version mismatch, missing code or unexpected package files."""
import argparse
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def check(root, dist, tag=None):
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if tag is not None and tag != "v" + version:
        raise ValueError(f"Tag {tag!r} does not match project version {version!r}")
    package = project["name"].replace("-", "_")
    expected = {p.relative_to(root).as_posix(): p.read_bytes().replace(b"\r\n", b"\n")
                for p in (root / package).rglob("*.py")}
    if not expected:
        raise ValueError("No package sources found")
    wheels, sdists = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and one sdist in a clean output directory")
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    with tarfile.open(sdists[0]) as archive:
        sdist = {"/".join(PurePosixPath(m.name).parts[1:]): archive.extractfile(m).read()
                 for m in archive.getmembers() if m.isfile()}
    for kind, files in (("wheel", wheel), ("sdist", sdist)):
        for name in files:
            parts = PurePosixPath(name).parts
            top = parts[0]
            if ".." in parts or name.startswith("/"):
                raise ValueError(f"Unsafe archive path: {name}")
            metadata = top.endswith(".dist-info") if kind == "wheel" else top.endswith(".egg-info")
            allowed_root = kind == "sdist" and name in {"pyproject.toml", "README.md", "LICENSE", "LICENSE.md", "setup.cfg", "PKG-INFO"}
            if top != package and not metadata and not allowed_root:
                raise ValueError(f"Unexpected {kind} file: {name}")
            if top == package and (not name.endswith(".py") or any(p.startswith(".") for p in parts)):
                raise ValueError(f"Unexpected package data: {name}")
        sources = {name: content.replace(b"\r\n", b"\n") for name, content in files.items()
                   if name.startswith(package + "/") and name.endswith(".py")}
        if sources != expected:
            raise ValueError(f"{kind} Python sources differ from checkout")
        metadata_paths = [n for n in files if n.endswith(".dist-info/METADATA")] if kind == "wheel" else ["PKG-INFO"]
        if len(metadata_paths) != 1:
            raise ValueError(f"Missing or ambiguous {kind} metadata")
        metadata = BytesParser().parsebytes(files[metadata_paths[0]])
        if metadata["Version"] != version or metadata["Name"] != project["name"]:
            raise ValueError(f"{kind} metadata differs from pyproject")
    print(f"PASS: {project['name']} {version}, {len(expected)} modules, wheel + sdist" + (f", tag {tag}" if tag else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--tag")
    args = parser.parse_args()
    check(args.root.resolve(), args.dist.resolve(), args.tag)

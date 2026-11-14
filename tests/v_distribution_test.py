"""Release gate rejects mismatched tags, omitted sources and accidental private data."""
from _bootstrap import REPO, TMP

import importlib.util
from io import BytesIO
import tarfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("distribution_gate", REPO / "tools/check_distribution.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.root = TMP / self._testMethodName
        self.dist = self.root / "dist"
        self.dist.mkdir(parents=True)
        (self.root / "example_plugin").mkdir()
        (self.root / "example_plugin/__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text('[project]\nname="example-plugin"\nversion="1.0.0b1"\n', encoding="utf-8")

    def build(self, extra=None, missing=False, version="1.0.0b1"):
        sources = {} if missing else {"example_plugin/__init__.py": b"VALUE = 1\n"}
        metadata = f"Name: example-plugin\nVersion: {version}\n".encode()
        with zipfile.ZipFile(self.dist / "example.whl", "w") as archive:
            for name, content in {**sources, "example_plugin-1.0.0b1.dist-info/METADATA": metadata, **(extra or {})}.items():
                archive.writestr(name, content)
        with tarfile.open(self.dist / "example.tar.gz", "w:gz") as archive:
            for name, content in {**sources, "PKG-INFO": metadata}.items():
                info = tarfile.TarInfo("example-1.0.0b1/" + name)
                info.size = len(content)
                archive.addfile(info, BytesIO(content))

    def test_valid_archives_and_tag(self):
        self.build()
        gate.check(self.root, self.dist, "v1.0.0b1")

    def test_wrong_tag(self):
        self.build()
        with self.assertRaises(ValueError):
            gate.check(self.root, self.dist, "v1.0.0b2")

    def test_missing_source(self):
        self.build(missing=True)
        with self.assertRaises(ValueError):
            gate.check(self.root, self.dist)

    def test_private_data_and_tests_are_rejected(self):
        for name in (".env.dev", "tests/test.py", "example_plugin/data.json"):
            self.build(extra={name: b"private"})
            with self.assertRaises(ValueError):
                gate.check(self.root, self.dist)

    def test_metadata_version_mismatch(self):
        self.build(version="1.0.0b2")
        with self.assertRaises(ValueError):
            gate.check(self.root, self.dist)


if __name__ == "__main__":
    unittest.main()

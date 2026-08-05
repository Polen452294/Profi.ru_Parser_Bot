from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from audit_dependencies import project_packages, query_osv


class FakeResponse:
    def __init__(self, payload):
        self.buffer = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        return self.buffer.read()


class DependencyAuditTests(unittest.TestCase):
    def test_osv_findings_are_mapped_to_package(self):
        response = FakeResponse(
            {
                "results": [
                    {"vulns": [{"id": "GHSA-test"}]},
                    {},
                ]
            }
        )
        with patch("audit_dependencies.urlopen", return_value=response):
            findings = query_osv([("package-a", "1.0"), ("package-b", "2.0")])

        self.assertEqual(findings, [("package-a", "1.0", ["GHSA-test"])])

    def test_only_project_dependency_tree_is_audited(self):
        class FakeDistribution:
            def __init__(self, name, version, requires=None):
                self.metadata = {"Name": name}
                self.version = version
                self.requires = requires or []

        installed = [
            FakeDistribution("Root-Package", "1.0", ["child_package>=2"]),
            FakeDistribution("child-package", "2.0"),
            FakeDistribution("unrelated-package", "9.9"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            requirements = Path(directory) / "requirements.txt"
            requirements.write_text("Root_Package[extra]==1.0\n", encoding="utf-8")
            packages = project_packages(requirements, installed)

        self.assertEqual(
            packages,
            [("child-package", "2.0"), ("Root-Package", "1.0")],
        )


if __name__ == "__main__":
    unittest.main()

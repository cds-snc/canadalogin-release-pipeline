from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from acceptance import catalog


class AcceptanceCatalogTests(unittest.TestCase):
    def test_current_catalog_is_valid(self) -> None:
        catalog.validate_catalog(catalog.discover())

    def test_expected_failure_requires_notification_capture_resources(self) -> None:
        source = catalog.TESTS_ROOT / "docker-build-failure"
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / source.name
            shutil.copytree(source, package)
            manifest_path = package / "test.yml"
            manifest = yaml.safe_load(manifest_path.read_text())
            del manifest["verification"]["resources"]["notification_capture_function"]
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

            with self.assertRaisesRegex(
                catalog.CatalogError, "notification_capture_function"
            ):
                catalog._load_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
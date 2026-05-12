from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codeisland_linux.opencode_plugin import install_plugin, merge_plugin_reference, remove_plugin_reference


class OpenCodePluginInstallTests(unittest.TestCase):
    def test_merge_plugin_reference_is_idempotent(self) -> None:
        config = {
            "$schema": "https://opencode.ai/config.json",
            "plugin": ["existing-plugin", "file:///old/codeisland-linux.js"],
        }

        first = merge_plugin_reference(config, plugin_ref="file:///tmp/codeisland-linux.js")
        second = merge_plugin_reference(first, plugin_ref="file:///tmp/codeisland-linux.js")

        self.assertEqual(second["plugin"], ["existing-plugin", "file:///tmp/codeisland-linux.js"])

    def test_remove_plugin_reference_preserves_other_plugins(self) -> None:
        config = {
            "plugin": ["existing-plugin", "file:///tmp/codeisland-linux.js"],
        }

        removed = remove_plugin_reference(config)

        self.assertEqual(removed["plugin"], ["existing-plugin"])

    def test_install_writes_plugin_and_updates_opencode_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "opencode.json"
            plugin_path = root / "plugins" / "codeisland-linux.js"
            config_path.write_text(json.dumps({"plugin": ["existing-plugin"]}), encoding="utf-8")

            result = install_plugin(config_path=config_path, plugin_path=plugin_path)

            installed = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(result["plugin_ref"], f"file://{plugin_path}")
            self.assertTrue(plugin_path.exists())
            self.assertIn("existing-plugin", installed["plugin"])
            self.assertIn(f"file://{plugin_path}", installed["plugin"])
            self.assertIn("codeisland-opencode-linux", plugin_path.read_text(encoding="utf-8"))

    def test_install_seeds_plugins_from_sibling_jsonc_when_json_has_no_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "opencode.json"
            plugin_path = root / "plugins" / "codeisland-linux.js"
            config_path.write_text(json.dumps({"$schema": "https://opencode.ai/config.json"}), encoding="utf-8")
            (root / "opencode.jsonc").write_text(json.dumps({"plugin": ["jsonc-plugin"]}), encoding="utf-8")

            install_plugin(config_path=config_path, plugin_path=plugin_path)

            installed = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(installed["plugin"], ["jsonc-plugin", f"file://{plugin_path}"])

    def test_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "opencode.json"
            plugin_path = root / "plugins" / "codeisland-linux.js"

            result = install_plugin(config_path=config_path, plugin_path=plugin_path, dry_run=True)

            self.assertTrue(result["dry_run"])
            self.assertFalse(config_path.exists())
            self.assertFalse(plugin_path.exists())

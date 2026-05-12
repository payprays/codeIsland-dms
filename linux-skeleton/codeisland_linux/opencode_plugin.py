from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from .protocol import default_socket_path


PLUGIN_FILENAME = "codeisland-linux.js"
PLUGIN_ID = "codeisland-linux"
RESOURCE_PATH = Path(__file__).parent / "resources" / "codeisland-opencode-linux.js"


class OpenCodePluginInstallError(Exception):
    pass


def default_opencode_config_path() -> Path:
    return Path("~/.config/opencode/opencode.json").expanduser()


def default_opencode_plugin_path() -> Path:
    return Path("~/.config/opencode/plugins").expanduser() / PLUGIN_FILENAME


def install_plugin(
    *,
    config_path: Path | None = None,
    plugin_path: Path | None = None,
    backup: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    config_path = (config_path or default_opencode_config_path()).expanduser()
    plugin_path = (plugin_path or default_opencode_plugin_path()).expanduser()
    plugin_source = RESOURCE_PATH.read_text(encoding="utf-8")
    config = _read_config(config_path)
    if "plugin" not in config:
        sibling_plugins = _read_sibling_jsonc_plugins(config_path)
        if sibling_plugins:
            config["plugin"] = sibling_plugins
    merged = merge_plugin_reference(config, plugin_ref=f"file://{plugin_path}")

    if dry_run:
        return {
            "config_path": str(config_path),
            "plugin_path": str(plugin_path),
            "plugin_ref": f"file://{plugin_path}",
            "config": merged,
            "dry_run": True,
        }

    plugin_path.parent.mkdir(parents=True, exist_ok=True)
    plugin_path.write_text(plugin_source, encoding="utf-8")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if backup and config_path.exists():
        shutil.copy2(config_path, config_path.with_suffix(config_path.suffix + ".bak"))
    config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "config_path": str(config_path),
        "plugin_path": str(plugin_path),
        "plugin_ref": f"file://{plugin_path}",
        "dry_run": False,
    }


def uninstall_plugin(
    *,
    config_path: Path | None = None,
    plugin_path: Path | None = None,
    remove_file: bool = False,
    backup: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    config_path = (config_path or default_opencode_config_path()).expanduser()
    plugin_path = (plugin_path or default_opencode_plugin_path()).expanduser()
    config = _read_config(config_path)
    merged = remove_plugin_reference(config)

    if dry_run:
        return {
            "config_path": str(config_path),
            "plugin_path": str(plugin_path),
            "config": merged,
            "remove_file": remove_file,
            "dry_run": True,
        }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if backup and config_path.exists():
        shutil.copy2(config_path, config_path.with_suffix(config_path.suffix + ".bak"))
    config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if remove_file and plugin_path.exists():
        plugin_path.unlink()
    return {
        "config_path": str(config_path),
        "plugin_path": str(plugin_path),
        "remove_file": remove_file,
        "dry_run": False,
    }


def merge_plugin_reference(config: dict[str, Any], *, plugin_ref: str) -> dict[str, Any]:
    merged = dict(config)
    plugins = merged.get("plugin")
    if not isinstance(plugins, list):
        plugins = []
    normalized_plugins = [item for item in plugins if isinstance(item, str) and PLUGIN_ID not in item and PLUGIN_FILENAME not in item]
    normalized_plugins.append(plugin_ref)
    merged["plugin"] = normalized_plugins
    merged.setdefault("$schema", "https://opencode.ai/config.json")
    return merged


def remove_plugin_reference(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    plugins = merged.get("plugin")
    if not isinstance(plugins, list):
        return merged
    filtered = [item for item in plugins if not (isinstance(item, str) and (PLUGIN_ID in item or PLUGIN_FILENAME in item))]
    if filtered:
        merged["plugin"] = filtered
    else:
        merged.pop("plugin", None)
    return merged


def _read_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {"$schema": "https://opencode.ai/config.json"}
    try:
        decoded = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OpenCodePluginInstallError(f"invalid OpenCode config JSON at {config_path}: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise OpenCodePluginInstallError(f"OpenCode config must be a JSON object: {config_path}")
    return decoded


def _read_sibling_jsonc_plugins(config_path: Path) -> list[str]:
    jsonc_path = config_path.with_suffix(".jsonc")
    if not jsonc_path.exists():
        return []
    try:
        decoded = json.loads(jsonc_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, dict):
        return []
    plugins = decoded.get("plugin")
    if not isinstance(plugins, list):
        return []
    return [item for item in plugins if isinstance(item, str)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the CodeIsland Linux OpenCode plugin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install and register the OpenCode plugin")
    install.add_argument("--config-path", default=str(default_opencode_config_path()))
    install.add_argument("--plugin-path", default=str(default_opencode_plugin_path()))
    install.add_argument("--socket-path", default=default_socket_path(), help="Documented for systemd/env wiring; the plugin also honors CODEISLAND_SOCKET_PATH at runtime")
    install.add_argument("--backup", action="store_true")
    install.add_argument("--dry-run", action="store_true")

    uninstall = subparsers.add_parser("uninstall", help="Remove the OpenCode plugin registration")
    uninstall.add_argument("--config-path", default=str(default_opencode_config_path()))
    uninstall.add_argument("--plugin-path", default=str(default_opencode_plugin_path()))
    uninstall.add_argument("--remove-file", action="store_true")
    uninstall.add_argument("--backup", action="store_true")
    uninstall.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "install":
        result = install_plugin(
            config_path=Path(args.config_path),
            plugin_path=Path(args.plugin_path),
            backup=args.backup,
            dry_run=args.dry_run,
        )
        result["socket_path"] = args.socket_path
    else:
        result = uninstall_plugin(
            config_path=Path(args.config_path),
            plugin_path=Path(args.plugin_path),
            remove_file=args.remove_file,
            backup=args.backup,
            dry_run=args.dry_run,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

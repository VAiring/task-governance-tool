"""Read-only selection of the exact locally materialized bundled MCP wrapper.

This is not Codex's effective configuration or permission resolver. Automatic
mode supports one observed bundled layout and rejects relevant overrides.
It never scans versions, executes manifest commands, installs or repairs files.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import stat
import tomllib
from .review_wait_mcp_relay import strict_json_loads, RelayError


PLUGIN = "codex-app-tools"
MARKET = "openai-bundled"
PLUGIN_KEY = PLUGIN + "@" + MARKET
MAX_METADATA_BYTES = 262_144
MAX_SERVER_BYTES = 8_388_608


class SelectionError(ValueError):
    def __init__(self):
        super().__init__("host_server_selection_unavailable")


@dataclass(frozen=True)
class AutoServer:
    project_root: Path

    def __post_init__(self):
        path = Path(self.project_root)
        if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
            raise ValueError("invalid_configuration")
        object.__setattr__(self, "project_root", path)


@dataclass(frozen=True)
class SelectedServer:
    path: Path
    version: str
    fingerprint: tuple


def _physical(path, *, missing=False):
    """Check every component; directory links/reparse points are not roots."""
    if not path.is_absolute() or ".." in path.parts:
        raise SelectionError()
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if missing:
                return False
            raise SelectionError() from None
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise SelectionError()
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise SelectionError()
    return True


def _read(path, limit):
    _physical(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise SelectionError()
    with path.open("rb") as stream:
        import os
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise SelectionError()
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _physical(path)
    if (len(data) > limit or not os.path.samestat(after, path.stat())
            or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise SelectionError()
    return data


def _json(data):
    try:
        return strict_json_loads(data)
    except RelayError:
        raise SelectionError() from None


def _relevant(data):
    return (PLUGIN_KEY in data.get("plugins", {})
            or MARKET in data.get("marketplaces", {}))


def _unsupported_plugin_override(plugin):
    if type(plugin) is not dict or set(plugin) - {"enabled", "mcp_servers"}:
        return True
    if "enabled" in plugin and plugin["enabled"] is not True:
        return True
    if "mcp_servers" not in plugin:
        return False
    servers = plugin["mcp_servers"]
    return not (type(servers) is dict and set(servers) == {"codex_app"}
                and type(servers["codex_app"]) is dict and set(servers["codex_app"]) == {"enabled"}
                and servers["codex_app"]["enabled"] is True)


def _unsupported_layer(data):
    return (MARKET in data.get("marketplaces", {})
            or _unsupported_plugin_override(data.get("plugins", {}).get(PLUGIN_KEY, {})))


def _configuration_paths(project_root):
    return tuple(root / ".codex/config.toml" for root in (project_root, *project_root.parents))


def select_server(mode: AutoServer, codex_home: Path) -> SelectedServer:
    """Resolve afresh per exchange, retaining only transient content hashes."""
    try:
        if type(mode) is not AutoServer:
            raise SelectionError()
        home = Path(codex_home)
        _physical(home)
        records = []

        def read(path, limit=MAX_METADATA_BYTES):
            data = _read(path, limit)
            records.append((str(path), hashlib.sha256(data).hexdigest()))
            return data

        config_path = home / "config.toml"
        config = tomllib.loads(read(config_path).decode("utf-8"))
        plugin = config.get("plugins", {}).get(PLUGIN_KEY, {})
        if plugin.get("enabled") is not True or _unsupported_plugin_override(plugin):
            raise SelectionError()
        if any(_relevant(profile) for profile in config.get("profiles", {}).values()):
            raise SelectionError()
        # Do not interpret a subset of effective project configuration as a
        # complete host policy. A relevant layer needs explicit-path setup.
        for path in _configuration_paths(mode.project_root):
            if path == config_path:
                continue
            if not _physical(path, missing=True):
                records.append((str(path), None))
                continue
            layer = tomllib.loads(read(path).decode("utf-8"))
            if (_unsupported_layer(layer) or any(_relevant(p) for p in layer.get("profiles", {}).values())):
                raise SelectionError()
        marketplace = config["marketplaces"][MARKET]
        source_root = home / ".tmp/bundled-marketplaces" / MARKET
        source_text = marketplace["source"]
        if type(source_text) is not str or marketplace.get("source_type") != "local":
            raise SelectionError()
        # Codex on Windows may serialize the same local root with a long-path
        # prefix. Do not resolve or inspect an arbitrary configured source.
        if source_text.startswith("\\\\?\\") and re.match(r"[A-Za-z]:[\\/]", source_text[4:]):
            source_text = source_text[4:]
        if Path(source_text) != source_root:
            raise SelectionError()
        catalog = _json(read(source_root / ".agents/plugins/marketplace.json"))
        if catalog["name"] != MARKET:
            raise SelectionError()
        entries = [p for p in catalog["plugins"] if p.get("name") == PLUGIN]
        if len(entries) != 1:
            raise SelectionError()
        source = entries[0]["source"]
        if source.get("source") != "local" or source.get("path") != "./plugins/" + PLUGIN:
            raise SelectionError()
        package_source = source_root / "plugins" / PLUGIN
        manifest = _json(read(package_source / ".codex-plugin/plugin.json"))
        version = manifest["version"]
        if (manifest["name"] != PLUGIN or manifest.get("mcpServers") != "./.mcp.json"
                or type(version) is not str or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
                or len(version) > 64):
            raise SelectionError()
        package_cache = home / "plugins/cache" / MARKET / PLUGIN / version
        if _json(read(package_cache / ".codex-plugin/plugin.json")) != manifest:
            raise SelectionError()
        for package in (package_source, package_cache):
            mcp = _json(read(package / ".mcp.json"))
            if (type(mcp) is not dict or type(mcp.get("mcpServers")) is not dict
                    or type(mcp["mcpServers"].get("codex_app")) is not dict):
                raise SelectionError()
        source_bytes = read(package_source / "server.mjs", MAX_SERVER_BYTES)
        server = package_cache / "server.mjs"
        if source_bytes != read(server, MAX_SERVER_BYTES):
            raise SelectionError()
        return SelectedServer(server, version, tuple(records))
    except SelectionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise SelectionError() from None


def validate_selection(mode, codex_home, selected):
    if select_server(mode, codex_home) != selected:
        raise SelectionError()

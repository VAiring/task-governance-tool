"""Owned stdio reader for the tested Desktop paginated history format."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import time

from . import review_wait_mcp_relay as relay
from ..review_answer import AnswerError


_TOTAL_BYTES = 64 * 1024 * 1024
_METHODS = {"thread/read", "thread/turns/list", "thread/items/list"}
_CLIENT = "taskgov-dispatch-reader"
_VERSION = "0.169.0-alpha.11.1"
_WINDOWS_PACKAGE = "OpenAI.Codex_26.1111.2228.0_x64__2p2nqsd0c76g0"
_STARTUP_NOTIFICATIONS = ["configWarning", "remoteControl/status/changed", "account/updated",
                          "account/rateLimits/updated", "mcpServer/startupStatus/updated"]


class _ItemSession(relay._ChildSession):
    stdout_limits = (_TOTAL_BYTES,)


def installed_runtime(home):
    """Only the host-selected bundled executable sharing the configured home."""
    executable = Path(os.environ.get("CODEX_APP_RUNTIME_CLI_PATH", ""))
    actual_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if os.name != "nt" or not executable.is_absolute():
        return None
    program_files = Path(os.environ.get("ProgramFiles", ""))
    if not program_files.is_absolute():
        return None
    try:
        if actual_home.resolve(strict=True) != home.resolve(strict=True):
            return None
        # Package files are readable even when Windows denies opening the
        # WindowsApps directory itself. Resolve the executable by its full path.
        apps = program_files.resolve(strict=True) / "WindowsApps"
        executable = executable.resolve(strict=True)
        package, app, resources, name = executable.relative_to(apps).parts
    except (ValueError, OSError):
        return None
    if package != _WINDOWS_PACKAGE or (app, resources, name) != ("app", "resources", "codex.exe"):
        return None
    return executable


@contextmanager
def open_reader(executable, home):
    child = None
    try:
        child = _ItemSession((str(executable), "app-server", "--listen", "stdio://"), executable.parent,
                             time.monotonic() + 60, 2, response_bytes=relay.MAX_READ_RESPONSE_BYTES,
                             stdout_bytes=_TOTAL_BYTES)
        serial = 0
        def exchange(method, params):
            nonlocal serial
            serial += 1
            child.send(relay._encode(dict(id=serial, method=method, params=params)))
            message = child._message(relay.MAX_READ_RESPONSE_BYTES)
            if (type(message) is not dict or set(message) != {"id", "result"} or
                    type(message["id"]) is not int or message["id"] != serial or type(message["result"]) is not dict):
                raise relay.RelayError()
            return message["result"]

        initialized = exchange("initialize", dict(clientInfo=dict(name=_CLIENT, version="1"),
            capabilities=dict(experimentalApi=True, optOutNotificationMethods=_STARTUP_NOTIFICATIONS)))
        agent = initialized.get("userAgent")
        if (type(agent) is not str or re.match(r"^[^/\r\n]{1,100}/" + re.escape(_VERSION) + r"(?: |$)", agent) is None
                or type(initialized.get("codexHome")) is not str
                or Path(initialized["codexHome"]).resolve() != home.resolve()):
            raise relay.RelayError()
        child.send(relay._encode(dict(method="initialized", params={})))

        def read(method, **params):
            if method not in _METHODS:
                raise relay.RelayError()
            return exchange(method, params)

        yield read
    except (OSError, ValueError, relay.RelayError):
        raise AnswerError("collection_unavailable", reason="transport_unavailable") from None
    finally:
        if child is not None and not child.close():
            raise AnswerError("collection_unavailable", reason="transport_unavailable") from None

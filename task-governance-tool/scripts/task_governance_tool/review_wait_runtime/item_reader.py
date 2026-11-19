"""Owned stdio reader for the host-selected Windows Desktop history API."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import time

from . import review_wait_mcp_relay as relay
from ..review_answer import AnswerError
from ..review_answer_item_pages import PaginationUnsupported


_TOTAL_BYTES = 64 * 1024 * 1024
_METHODS = {"thread/read", "thread/turns/list", "thread/items/list"}
_CLIENT = "taskgov-dispatch-reader"
_WINDOWS_PACKAGE = re.compile(r"OpenAI\.Codex_(?:[0-9]+\.){3}[0-9]+_(?:x64|arm64)__2p2nqsd0c76g0")
_STARTUP_NOTIFICATIONS = ["configWarning", "remoteControl/status/changed", "account/updated",
                          "account/rateLimits/updated", "mcpServer/startupStatus/updated"]


class _ItemSession(relay._ChildSession):
    stdout_limits = (_TOTAL_BYTES,)


def installed_runtime(home):
    """Only the host-selected bundled executable sharing the configured home."""
    selected = os.environ.get("CODEX_APP_RUNTIME_CLI_PATH")
    if os.name != "nt":
        raise PaginationUnsupported()
    if not selected:
        raise AnswerError("binding_mismatch", reason="identity")
    executable = Path(selected)
    actual_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    program_files = Path(os.environ.get("ProgramFiles", ""))
    return _bundled_runtime(executable, actual_home, home, program_files)


def _bundled_runtime(executable, actual_home, home, program_files):
    try:
        if (not all(p.is_absolute() for p in (executable, actual_home, home, program_files))
                or actual_home.resolve(strict=True) != home.resolve(strict=True)):
            raise ValueError
        # Package files are readable even when Windows denies opening the
        # WindowsApps directory itself. Resolve the executable by its full path.
        apps = program_files.resolve(strict=True) / "WindowsApps"
        executable = executable.resolve(strict=True)
        package, app, resources, name = executable.relative_to(apps).parts
        if (not _WINDOWS_PACKAGE.fullmatch(package) or (app, resources, name) != ("app", "resources", "codex.exe")
                or not executable.is_file()):
            raise ValueError
    except (ValueError, OSError):
        raise AnswerError("binding_mismatch", reason="identity") from None
    return executable


@contextmanager
def open_reader(executable, home):
    child = None
    try:
        child = _ItemSession((str(executable), "app-server", "--listen", "stdio://"), executable.parent,
                             time.monotonic() + 60, 2, response_bytes=relay.MAX_READ_RESPONSE_BYTES,
                             stdout_bytes=_TOTAL_BYTES)
        serial = 0
        supported = set()
        def exchange(method, params):
            nonlocal serial
            serial += 1
            child.send(relay._encode(dict(id=serial, method=method, params=params)))
            message = child._message(relay.MAX_READ_RESPONSE_BYTES)
            if type(message) is not dict or type(message.get("id")) is not int or message["id"] != serial:
                raise relay.RelayError()
            if set(message) == {"id", "error"} and method in _METHODS and method not in supported:
                error = message["error"]
                if (type(error) is dict and set(error) in ({"code", "message"}, {"code", "message", "data"})
                        and error.get("data") is None
                        and type(error["code"]) is int and error["code"] == -32601
                        and type(error["message"]) is str and error["message"].strip()):
                    raise PaginationUnsupported()
            if set(message) != {"id", "result"} or type(message["result"]) is not dict:
                raise relay.RelayError()
            supported.add(method)
            return message["result"]

        initialized = exchange("initialize", dict(clientInfo=dict(name=_CLIENT, version="1"),
            capabilities=dict(experimentalApi=True, optOutNotificationMethods=_STARTUP_NOTIFICATIONS)))
        agent = initialized.get("userAgent")
        if type(agent) is not str or not agent.strip() or len(agent) > 1024 or not agent.isprintable():
            raise relay.RelayError()
        declared = initialized.get("codexHome")
        if (type(declared) is not str or not Path(declared).is_absolute()
                or Path(declared).resolve(strict=True) != home.resolve(strict=True)):
            raise AnswerError("binding_mismatch", reason="identity")
        child.send(relay._encode(dict(method="initialized", params={})))

        def read(method, **params):
            if method not in _METHODS:
                raise relay.RelayError()
            return exchange(method, params)

        yield read
    except (OSError, ValueError, relay.RelayError):
        raise AnswerError("collection_unavailable", reason="transport_unavailable") from None
    finally:
        if child is not None:
            try:
                clean = child.close()
            except (OSError, ValueError, RuntimeError):
                clean = False
            if clean is not True:
                raise AnswerError("collection_unavailable", reason="transport_unavailable") from None

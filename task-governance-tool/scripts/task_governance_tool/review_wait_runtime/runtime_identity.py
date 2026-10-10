"""Read-only evidence of this MCP process's source and deployed package.

Only the review-wait entry installs this capture, before package imports. The
existing source-only loader supplies the bytes actually compiled. The startup
inventory also covers lazy imports and subprocess helpers in the shipped layout;
it represents loaded implementation only when imports agree and its entry source
compiles to the executing entry's code object. This is implementation equivalence,
not a claim to recover the interpreter's original entry source bytes.
No source, filename, exception or host metadata is returned by diagnosis.
"""

import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import threading
from types import CodeType


_PACKAGE = "task_governance_tool"
_DIRECTORIES = ("", _PACKAGE, _PACKAGE + "/review_wait_runtime")
_SCHEMA_FILE = _PACKAGE + "/storage.py"
_MAX_FILES = 512
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024


def _physical(value, kind):
    info = value.lstat()
    if (not kind(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("source_unavailable")
    return info


def _read_source(path):
    before = _physical(path, stat.S_ISREG)
    if before.st_size > _MAX_FILE_BYTES:
        raise ValueError("source_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        opened = os.fstat(source.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("source_unavailable")
        data = source.read(_MAX_FILE_BYTES + 1)
        after = os.fstat(source.fileno())
    final = _physical(path, stat.S_ISREG)
    if (len(data) > _MAX_FILE_BYTES or
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) !=
            (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
        raise ValueError("source_unavailable")
    return data


def _schema(source):
    tree = ast.parse(source)
    assignments = [node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION"
                for target in node.targets)]
    writes = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
              and node.id == "SCHEMA_VERSION" and isinstance(node.ctx, ast.Store)]
    if (len(assignments) != 1 or len(writes) != 1 or len(assignments[0].targets) != 1
            or not isinstance(assignments[0].value, ast.Constant)
            or type(assignments[0].value.value) is not int
            or not 0 < assignments[0].value.value < 65536):
        raise ValueError("source_unavailable")
    return assignments[0].value.value


def _digest(files):
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(b"taskgov-review-runtime-v1\0" + encoded).hexdigest()


def _snapshot(scripts, *, entry_code=None):
    files = {}
    total = 0
    schema = None
    for directory in _DIRECTORIES:
        root = scripts / directory
        _physical(root, stat.S_ISDIR)
        names = []
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.endswith(".py"):
                    names.append(entry.name)
                    if len(names) + len(files) > _MAX_FILES:
                        raise ValueError("source_unavailable")
        for name in sorted(names):
            if not name[:-3].isidentifier() or not name.isascii():
                raise ValueError("source_unavailable")
            relative = directory + "/" + name if directory else name
            data = _read_source(root / name)
            total += len(data)
            if len(files) >= _MAX_FILES or total > _MAX_TOTAL_BYTES:
                raise ValueError("source_unavailable")
            files[relative] = hashlib.sha256(data).hexdigest()
            if relative == _SCHEMA_FILE:
                schema = _schema(data)
            if relative == "review_wait_server.py" and entry_code is not None:
                # Compare the same bytes included in this snapshot against the
                # already executing module, not another later source reread.
                if (not isinstance(entry_code, CodeType) or
                        entry_code != compile(data, entry_code.co_filename, "exec", dont_inherit=True)):
                    raise ValueError("source_unavailable")
    required = {"source_imports.py", "review_wait_server.py", "review_handoff.py",
        _PACKAGE + "/__init__.py", _PACKAGE + "/review_wait_runtime/__init__.py",
        _PACKAGE + "/review_wait_runtime/runtime_identity.py", _SCHEMA_FILE}
    if not required.issubset(files) or schema is None:
        raise ValueError("source_unavailable")
    return files, schema


class RuntimeIdentity:
    """Transient source capture; diagnosis neither imports nor executes new code."""

    def __init__(self, scripts=None):
        self._scripts = Path(scripts).absolute() if scripts is not None else None
        self._files = None
        self._schema = None
        self._captured = set()
        self._unverified = False
        self._lock = threading.RLock()

    @classmethod
    def install(cls, scripts, *, bootstrap_source, entry_code=None):
        """Called only by the source-compiled entry bootstrap, before imports.

        Reuses source_imports.py without changing any other entry's loading.
        Failure to establish evidence leaves identity unknown, not a new startup
        gate. Source-only loading continues to have its existing import failures.
        """
        identity = cls(scripts)
        try:
            if not isinstance(entry_code, CodeType):
                identity._unverified = True
            identity._files, identity._schema = _snapshot(identity._scripts, entry_code=entry_code)
            identity._record_source(_PACKAGE + "/review_wait_runtime/runtime_identity.py",
                                    bootstrap_source)
        except Exception:
            identity._unverified = True
        if any(name == _PACKAGE or name.startswith(_PACKAGE + ".") for name in sys.modules):
            identity._unverified = True
        bootstrap = identity._scripts / "source_imports.py"
        source = _read_source(bootstrap)
        identity._record_source("source_imports.py", source)
        namespace = {"__file__": str(bootstrap)}
        exec(compile(source, str(bootstrap), "exec", dont_inherit=True), namespace)
        base = namespace["SourceLoader"]

        class CapturingLoader(base):
            def get_code(self, fullname):
                filename = self.get_filename(fullname)
                data = self.get_data(filename)
                relative = Path(filename).relative_to(identity._scripts).as_posix()
                with identity._lock:
                    identity._record_source(relative, data)
                    identity._captured.add(fullname)
                return compile(data, filename, "exec", dont_inherit=True)

        namespace["SourceLoader"] = CapturingLoader
        namespace["install"](identity._scripts)
        return identity

    def _record_source(self, relative, data):
        if (self._files is None or self._files.get(relative) != hashlib.sha256(data).hexdigest()):
            self._unverified = True

    def inspect(self, *, loaded_schema):
        """Return only hashes, schema integers and fixed status, never locations."""
        loaded_schema = loaded_schema if type(loaded_schema) is int and 0 < loaded_schema < 65536 else None
        result = {"version": 1, "code_id": None, "supported_schema": loaded_schema,
                  "deployed_supported_schema": None, "comparison": "unknown"}
        if self._scripts is None:
            return result
        try:
            with self._lock:
                modules = {name for name in sys.modules
                           if name == _PACKAGE or name.startswith(_PACKAGE + ".")}
                known = (not self._unverified and self._files is not None
                         and bool(modules) and modules.issubset(self._captured)
                         and loaded_schema == self._schema)
                if known:
                    result["code_id"] = _digest(self._files)
                files, ceiling = _snapshot(self._scripts)
                result["deployed_supported_schema"] = ceiling
                if known:
                    if result["code_id"] != _digest(files):
                        result["comparison"] = "different"
                    elif loaded_schema == ceiling:
                        result["comparison"] = "matching"
        except Exception:
            pass
        return result

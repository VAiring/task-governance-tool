"""Optional Codex PermissionRequest hook and write-free configuration proposal.

Only literal single-command shell syntax is understood. Everything else declines
to decide. The approved physical project, Python and package revision are fixed
by the reviewed hook definition; this module never executes a command or writes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

from task_governance_tool.task_record_policy import validate_record_arguments

LIMIT = 1048576
UTF8_POWERSHELL = "$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _json(raw):
    if len(raw) > LIMIT:
        raise ValueError
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def physical(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or str(path.absolute()) != str(path):
        raise ValueError
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError
    if not (stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode):
        raise ValueError
    if not directory and info.st_nlink != 1:
        raise ValueError
    return path


def runtime_digest(package):
    """Pin code and shipped assets, excluding mutable local config/state.

    Exact manifest bytes and every listed physical file are included. The file
    inventory is checked too, including non-source import artifacts. Existing
    __pycache__ is inert: the hook and admitted package use source-only imports.
    Python runs with -I -S -B (no site/PYTHONPATH).
    This is trusted-local drift detection, not a hostile-process execution lease.
    """
    manifest = physical(package / "release-manifest.json").read_bytes()
    document = _json(manifest)
    entries = document["core_files"]
    if type(entries) is not dict or not 1 <= len(entries) <= 4096:
        raise ValueError
    digest = hashlib.sha256(b"taskgov-preapproval-v1\0" + manifest)
    listed = set()
    total = 0
    for relative, expected in sorted(entries.items()):
        if (not isinstance(relative, str) or "\\" in relative
                or any(part in ("", ".", "..") for part in relative.split("/"))
                or Path(relative).is_absolute()):
            raise ValueError
        path = physical(package / relative)
        size = path.stat().st_size
        total += size
        if total > 64 * 1024 * 1024:
            raise ValueError
        raw = path.read_bytes()
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError
        digest.update(relative.encode("utf-8") + b"\0" + actual.encode("ascii") + b"\0")
        listed.add(relative)
    for path in (package / "scripts").rglob("*"):
        # Cache contents are never imported by the source-only package loader.
        # All other files must be shipped/pinned, including root-level legacy
        # bytecode and native extensions that could shadow standard imports.
        physical(path, directory=path.is_dir())
        if path.is_dir():
            continue
        if path.parent.name == "__pycache__" and path.suffix == ".pyc":
            continue
        if path.relative_to(package).as_posix() not in listed:
            raise ValueError
    return digest.hexdigest()


def literal_command(command, shell):
    """Decode a deliberately small, unambiguous subset; never evaluate shell.

    PowerShell supports the generated UTF-8 assignment and one literal here-
    string piped into the command. POSIX supports one quoted here-document.
    Their contents are input bytes, not approval data or executable syntax.
    """
    if not isinstance(command, str) or len(command.encode("utf-8")) > LIMIT or "\0" in command:
        raise ValueError
    command = command.replace("\r\n", "\n")
    if "\r" in command:
        raise ValueError
    if shell == "powershell":
        command = command.removeprefix(UTF8_POWERSHELL)
        if command.startswith("@'\n"):
            end = command.find("\n'@")
            if (end < 0 or not command[end + 3:].startswith(" | ")
                    or any(c in command[:end] for c in "‘’‚‛")
                    or re.search(r"(?m)^[ \t]*'@", command[:end])):
                raise ValueError
            command = command[end + 6:]
        command = command.strip()
        if command.startswith("& "):
            command = command[2:]
        # ASCII quotes only. Smart quotes and other PowerShell metacharacters
        # decline even inside literals rather than approximating PS parsing.
        pattern = re.compile(r"(?:'((?:[^'‘’‚‛]|'')*)'|([A-Za-z0-9_./:=+@,\\-]+))(?=\s|$)")
        words = []
        while command:
            match = pattern.match(command)
            if match is None:
                raise ValueError
            words.append(match[1].replace("''", "'") if match[1] is not None else match[2])
            command = command[match.end():]
            if command and command[0] not in " \t":
                raise ValueError
            command = command.lstrip(" \t")
        return words
    if shell != "posix":
        raise ValueError
    # Only the exact generated quoted heredoc is recognized; its delimiter
    # must end the command, with no following shell command or redirection.
    match = re.search(r" <<'([A-Z_]+)'\n", command)
    if match:
        end = "\n" + match[1]
        body = command[match.end():]
        if not body.endswith(end) or match[1] in body[:-len(end)].split("\n"):
            raise ValueError
        command = command[:match.start()]
    # Tokenize ourselves before shlex: only literal single quoted or simple
    # bare tokens, plus shlex's exact single-quote escape sequence, are allowed.
    cursor = 0
    while cursor < len(command):
        if command[cursor] in " \t":
            cursor += 1
        elif command[cursor] == "'":
            end = command.find("'", cursor + 1)
            if end < 0:
                raise ValueError
            cursor = end + 1
        elif command.startswith('"\'"', cursor):
            cursor += 3
        else:
            match = re.match(r"[A-Za-z0-9_./:=+@,\\-]+", command[cursor:])
            if not match:
                raise ValueError
            cursor += match.end()
    return shlex.split(command, posix=True)


def decide(event, *, repo, python, package, digest, shell):
    """Return allow only for the one pinned invocation; unknown means abstain."""
    try:
        if (type(event) is not dict or event.get("hook_event_name") != "PermissionRequest"
                or event.get("tool_name") != "Bash" or type(event.get("tool_input")) is not dict):
            return {}
        physical(repo, directory=True)
        physical(python)
        physical(package, directory=True)
        if str(physical(Path(event["cwd"]), directory=True)) != str(repo):
            return {}
        words = literal_command(event["tool_input"]["command"], shell)
        if len(words) < 6 or words[:4] != [str(python), "-I", "-S", "-B"]:
            return {}
        script = Path(words[4])
        if str(script) == str(package / "scripts/read_reference.py"):
            if len(words) != 6 or not re.fullmatch(r"references/[a-z_]+\.md#[a-z0-9-]+", words[5]):
                return {}
        else:
            if str(script) not in (str(package / "scripts/taskgov.py"), str(package / "scripts/review_handoff.py")):
                return {}
            bound = validate_record_arguments(script.name, words[5:])
            if not Path(bound).is_absolute() or str(physical(Path(bound), directory=True)) != str(repo):
                return {}
        if runtime_digest(package) != digest:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "allow"}}}
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return {}


def shell_command(words, shell):
    if shell == "powershell":
        if any(any(c in word for c in "‘’‚‛\0") for word in words):
            raise ValueError
        return "& " + " ".join("'" + word.replace("'", "''") + "'" for word in words)
    return shlex.join(words)


def propose(existing, *, repo, python, package, shell):
    """Return a preserving hooks.json candidate; never install or trust it."""
    if type(existing) is not dict or type(existing.get("hooks", {})) is not dict:
        raise ValueError
    physical(repo, directory=True)
    physical(python)
    physical(package, directory=True)
    if any(any(c in str(path) for c in "‘’‚‛\r\n\0") for path in (repo, python, package)):
        raise ValueError
    if str(package) not in (str(repo / ".agents/skills/task-governance-tool"), str(repo / "task-governance-tool")):
        raise ValueError
    digest = runtime_digest(package)
    entry = package / "scripts/task_preapproval.py"
    arguments = [str(entry), "hook", "--repo", str(repo), "--python", str(python),
                 "--shell", shell, "--runtime-digest", digest]
    # Hook trust covers the definition, not referenced script contents. Pin the
    # only non-stdlib files loaded before runtime_digest can run, using
    # immutable code embedded in that reviewed definition. Never import changed
    # policy code merely in order to ask it whether the change is approved.
    pins = {str(physical(package / relative)): hashlib.sha256((package / relative).read_bytes()).hexdigest()
            for relative in ("scripts/task_preapproval.py", "scripts/source_imports.py", "scripts/task_governance_tool/__init__.py", "scripts/task_governance_tool/task_preapproval.py",
                             "scripts/task_governance_tool/task_record_policy.py")}
    bootstrap = (
        "import hashlib,runpy,stat,sys\nfrom pathlib import Path\n"
        + "pins=" + repr(pins) + "\n"
        + "try:\n"
        + " for name,digest in pins.items():\n"
        + "  p=Path(name)\n"
        + "  for part in (*p.parents,p):\n"
        + "   s=part.lstat()\n"
        + "   if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&1024: raise ValueError()\n"
        + "  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>1048576: raise ValueError()\n"
        + "  if hashlib.sha256(p.read_bytes()).hexdigest()!=digest: raise ValueError()\n"
        + "except (OSError,ValueError):\n print('{}');sys.exit(0)\n"
        + "sys.argv=" + repr(arguments) + "\nrunpy.run_path(sys.argv[0],run_name='__main__')"
    )
    # The trusted hook command is fixed code, never an allow rule for Python -c.
    # Newlines are data inside the shell literal, not a compound tool command.
    if shell == "powershell":
        command = "& " + " ".join("'" + value.replace("'", "''") + "'"
                                     for value in [str(python), "-I", "-S", "-B", "-c", bootstrap])
        command = "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + base64.b64encode(command.encode("utf-16le")).decode("ascii")
        if len(command) > 8000:
            raise ValueError
    else:
        command = shlex.join([str(python), "-I", "-S", "-B", "-c", bootstrap])
    candidate = json.loads(json.dumps(existing))
    hooks = candidate.setdefault("hooks", {})
    groups = hooks.setdefault("PermissionRequest", [])
    if type(groups) is not list:
        raise ValueError
    group = {"matcher": "^Bash$", "hooks": [{"type": "command", "command": command, "timeout": 10}]}
    if shell == "powershell":
        group["hooks"][0]["commandWindows"] = command
    if group not in groups:
        groups.append(group)
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("hook", "propose"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--shell", choices=("powershell", "posix"), required=True)
    parser.add_argument("--runtime-digest")
    parser.add_argument("--existing-hooks", help="read existing hooks.json; propose emits a merged candidate only")
    args = parser.parse_args(argv)
    package = Path(__file__).absolute().parents[2]
    try:
        repo, python = Path(args.repo), Path(args.python)
        if args.operation == "hook":
            value = decide(_json(sys.stdin.buffer.read(LIMIT + 1)), repo=repo, python=python,
                           package=package, digest=args.runtime_digest, shell=args.shell)
        else:
            existing = _json(Path(args.existing_hooks).read_bytes()) if args.existing_hooks else {}
            value = propose(existing, repo=repo, python=python, package=package, shell=args.shell)
        print(json.dumps(value, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print("{}" if args.operation == "hook" else '{"error":"preapproval_proposal_unavailable"}')
        return 0 if args.operation == "hook" else 1

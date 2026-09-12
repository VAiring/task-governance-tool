"""Read one linked package section; no taskgov, project, or state access."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


REFERENCES = {"task_workflow.md", "cli_contracts.md", "reconciliation.md"}


def section(text: str, fragment: str) -> str:
    """Keep the selected subtree and ancestor introductions, without siblings."""
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int, set[str]]] = []
    pending: set[str] = set()
    counts: dict[str, int] = {}
    fence = ""
    for position, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not fence:
                fence = marker
            elif fence == marker:
                fence = ""
            continue
        if fence:
            continue
        anchor = re.fullmatch(r'<a id="([\w-]+)"></a>', stripped)
        if anchor:
            pending.add(anchor[1])
            continue
        heading = re.match(r"^(#{1,6}) (.+?)\s*$", line)
        if heading:
            base = re.sub(r"[^\w\s-]", "", heading[2].lower())
            base = re.sub(r"\s+", "-", base).strip("-")
            ordinal = counts.get(base, 0)
            counts[base] = ordinal + 1
            slug = base if ordinal == 0 else f"{base}-{ordinal}"
            headings.append((position, len(heading[1]), pending | {slug}))
            pending = set()
        elif stripped:
            # Explicit anchors in these references immediately precede headings.
            pending = set()
    matches = [i for i, (_, _, names) in enumerate(headings) if fragment in names]
    if len(matches) != 1:
        raise ValueError("Reference section is missing or ambiguous.")
    chosen = matches[0]
    start, level, _ = headings[chosen]
    end = next((p for p, depth, _ in headings[chosen + 1:] if depth <= level), len(lines))
    ancestors: list[int] = []
    for i in range(chosen):
        while ancestors and headings[ancestors[-1]][1] >= headings[i][1]:
            ancestors.pop()
        ancestors.append(i)
    while ancestors and headings[ancestors[-1]][1] >= level:
        ancestors.pop()
    pieces = ["".join(lines[headings[i][0]:headings[i + 1][0]]) for i in ancestors]
    pieces.append("".join(lines[start:end]))
    return "\n".join(piece.rstrip() for piece in pieces) + "\n"


def read_reference(target: str, package: Path) -> str:
    path, separator, fragment = target.partition("#")
    if (not separator or not fragment or path not in
            {f"references/{name}" for name in REFERENCES}):
        raise ValueError("Use a package reference link with an exact section fragment.")
    content = (package / path).read_text(encoding="utf-8")
    body = section(content, fragment)
    return f"Source: {target}\nLinks resolve relative to {path}.\n\n{body}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", help="package-relative file#section from an existing link")
    args = parser.parse_args()
    try:
        output = read_reference(args.reference, Path(__file__).resolve().parents[1])
    except (OSError, UnicodeError, ValueError):
        print("Reference unavailable: use an existing package file#section link.", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(output.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

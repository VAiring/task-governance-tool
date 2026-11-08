"""Immutable, invocation-local bytes for independently writable Git fixtures."""

from dataclasses import dataclass
from pathlib import Path
import stat


@dataclass(frozen=True)
class GitProjectImage:
    directories: tuple[str, ...]
    files: tuple[tuple[str, bytes, int], ...]

    @classmethod
    def capture(cls, repo: Path):
        directories, files = [], []
        for path in sorted(repo.rglob("*")):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Git fixture images require ordinary physical entries")
            relative = path.relative_to(repo).as_posix()
            mode = path.stat().st_mode
            if stat.S_ISDIR(mode):
                directories.append(relative)
            elif stat.S_ISREG(mode):
                files.append((relative, path.read_bytes(), stat.S_IMODE(mode)))
            else:
                raise ValueError("Git fixture images require regular files")
        return cls(tuple(directories), tuple(files))

    def materialize(self, repo: Path):
        repo.mkdir()
        for relative in self.directories:
            (repo / relative).mkdir()
        for relative, content, mode in self.files:
            path = repo / relative
            with path.open("xb") as output:
                output.write(content)
            path.chmod(mode)

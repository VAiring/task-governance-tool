"""Source-only package imports for the optional preapproval invocation.

Entry points compile this file directly before importing any package module.
Never use a normal import for this bootstrap: that would consult bytecode first.
"""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys


# This is the shipped layout, not a filesystem discovery rule. A new directory
# can never change the source selected for a previously approved module name.
PACKAGES = frozenset(("task_governance_tool", "task_governance_tool.review_wait_runtime"))


class SourceLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        source = self.get_filename(fullname)
        return compile(self.get_data(source), source, "exec", dont_inherit=True)


class PackageSourceFinder:
    def __init__(self, scripts):
        self.scripts = scripts

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "task_governance_tool" and not fullname.startswith("task_governance_tool."):
            return None
        parts = fullname.split(".")
        if not all(part.isidentifier() for part in parts):
            raise ImportError("Invalid package module")
        package = fullname in PACKAGES
        if not package and fullname.rpartition(".")[0] not in PACKAGES:
            raise ImportError("Package layout unavailable")
        location = self.scripts.joinpath(*parts)
        source = location / "__init__.py" if package else location.with_suffix(".py")
        if not source.is_file():
            raise ImportError("Package source unavailable")
        return importlib.util.spec_from_file_location(fullname, source,
            loader=SourceLoader(fullname, str(source)),
            submodule_search_locations=[str(location)] if package else None)


def install(scripts):
    sys.meta_path.insert(0, PackageSourceFinder(Path(scripts)))

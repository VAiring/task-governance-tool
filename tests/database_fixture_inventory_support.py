"""Read-only inventory of test initialization and its statically reachable users.

The inventory is review material, not a dispatcher or a test-selection policy.
Conditional runtime pins and instance-based calls still require source review.
"""
from __future__ import annotations

import ast
from functools import cache
from pathlib import Path


OPERATIONS = {
    "initialize_database", "initialize_uuid_database", "apply_migrations",
    "initialize_taskgov_internal", "initialize_current_test_database",
    "initialize_current_test_target", "review_fixture",
    "initialize_feature_test_target",
    "initialize_current",
    "initialize_case_database", "initialize_case",
}


def _initialization_symbol(name):
    return (name.split(".")[-1] in OPERATIONS or
            name == "tests.verification_receipt_test_support.initialize")


def _scope_nodes(nodes):
    """Visit one Python binding scope, leaving nested definitions separate."""
    for node in nodes:
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _scope_nodes(ast.iter_child_nodes(node))


def _scope_imports(nodes):
    aliases = {}
    for node in _scope_nodes(nodes):
        if isinstance(node, ast.ImportFrom) and node.module:
            for name in node.names:
                aliases[name.asname or name.name] = node.module + "." + name.name
        elif isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
    return aliases


def _scoped_calls(nodes, outer_aliases):
    aliases = {**outer_aliases, **_scope_imports(nodes)}
    for node in _scope_nodes(nodes):
        if isinstance(node, ast.Call):
            yield node, aliases
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _scoped_calls(node.body, aliases)
        elif isinstance(node, ast.Lambda):
            yield from _scoped_calls([node.body], aliases)


def inventory(root: Path):
    functions, imports, classes, all_imports = {}, {}, {}, {}
    sites = []
    for path in sorted((root / "tests").glob("*.py")):
        module = "tests." + path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = imports.setdefault(module, _scope_imports(tree.body))
        all_imports[module] = {value for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for value in _scope_imports([node]).values()}

        def visit(nodes, parents=()):
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    key = module + "." + ".".join((*parents, node.name))
                    classes[key] = [ast.unparse(base) for base in node.bases]
                    visit(node.body, (*parents, node.name))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    key = module + "." + ".".join((*parents, node.name))
                    calls = []
                    for call, call_aliases in _scoped_calls(node.body, aliases):
                        expression = ast.unparse(call.func)
                        parts = expression.split(".")
                        if parts[0] in ("self", "cls") and parents:
                            resolved = module + "." + ".".join((*parents, *parts[1:]))
                        else:
                            resolved = call_aliases.get(parts[0], module + "." + parts[0])
                            resolved += "".join("." + part for part in parts[1:])
                        calls.append(resolved)
                        alternatives = []
                        if isinstance(call.func, ast.IfExp):
                            for branch in (call.func.body, call.func.orelse):
                                if isinstance(branch, ast.Name):
                                    alternatives.append(call_aliases.get(branch.id, module + "." + branch.id))
                            calls.extend(alternatives)
                        operation = resolved.split(".")[-1]
                        constructor = (call.func.value.func
                            if isinstance(call.func, ast.Attribute)
                            and call.func.attr == "initialize"
                            and isinstance(call.func.value, ast.Call) else None)
                        template_constructor = (
                            isinstance(constructor, ast.Name) and
                            call_aliases.get(constructor.id, constructor.id).split(".")[-1] == "EmptyDatabaseTemplate"
                            or isinstance(constructor, ast.Attribute) and
                            constructor.attr == "EmptyDatabaseTemplate")
                        if (_initialization_symbol(resolved) or any(map(_initialization_symbol, alternatives)) or template_constructor or
                            (operation == "initialize" and
                             (expression.startswith("EmptyDatabaseTemplate(") or
                              expression.startswith("template."))) or
                            (operation == "initializer" and
                             "initialize_current_test_database" in ast.unparse(node))):
                            sites.append(dict(file=path.relative_to(root).as_posix(),
                                function=".".join((*parents, node.name)),
                                call=ast.unparse(call), operation=("conditional_initializer" if alternatives else operation)))
                    functions[key] = calls
        visit(tree.body)

    # Resolve reexports as well as direct imports before computing reachability.
    @cache
    def resolve(name):
        seen = set()
        while name not in seen:
            seen.add(name)
            parts = name.split(".")
            match = next(((".".join(parts[:index]), ".".join(parts[index:]))
                          for index in range(len(parts)-1, 0, -1)
                          if ".".join(parts[:index]) in imports), None)
            if match is None:
                return name
            module, tail = match
            first, *rest = tail.split(".")
            if first not in imports[module]:
                return name
            name = imports[module][first] + "".join("." + part for part in rest)
        return name

    def resolve_call(name):
        name = resolve(name)
        return name + ".__init__" if name + ".__init__" in functions else name

    # Sites also include explicit instance/conditional calls whose spelling is
    # not a global initializer symbol. They are roots of reachability too.
    direct_functions = {"tests." + Path(row["file"]).stem + "." + row["function"]
                        for row in sites}
    reached = set(direct_functions)
    while True:
        additional = {name for name, calls in functions.items()
                      if any(resolve_call(call) in reached for call in calls)} - reached
        if not additional:
            break
        reached.update(additional)
    indirect = sorted(reached - direct_functions)
    # Include import-only/instance/inheritance candidates for human disposition.
    files = {site["file"] for site in sites}
    modules = {"tests." + Path(file).stem for file in files}
    while True:
        added = {module for module, values in all_imports.items()
                 if any(any(value == used or value.startswith(used + ".")
                            for used in modules) for value in values)} - modules
        if not added:
            break
        modules.update(added)
    return dict(direct=sorted(sites, key=lambda row: (row["file"], row["function"], row["call"])),
                indirect=indirect,
                indirect_routes={name: sorted({resolve_call(call) for call in functions[name]
                    if resolve_call(call) in reached}) for name in indirect},
                import_candidates=sorted("tests/" + module[6:] + ".py" for module in modules),
                inherited_classes={name: bases for name, bases in classes.items()
                                   if any(base not in ("unittest.TestCase", "object")
                                          for base in bases)})

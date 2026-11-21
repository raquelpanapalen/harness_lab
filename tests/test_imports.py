"""Offline: every import in the harness resolves, including imports inside functions (only run when a task of that
family comes along, so a unit test can miss them). Catches stale module paths after a rename or move.
python tests/test_imports.py"""
import ast
import importlib
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PACKAGES = ["harness", "arena", "baseline"]
problems = []
checked = 0
for pkg in PACKAGES:
    for path in sorted((ROOT / pkg).rglob("*.py")):
        rel = path.relative_to(ROOT).with_suffix("")
        parts = list(rel.parts)
        is_init = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_init else parts)
        package = module if is_init else module.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.ImportFrom):
                base = ("." * node.level + (node.module or "")) if node.level else node.module
                try:
                    target = importlib.util.resolve_name(base, package) if node.level else base
                    mod = importlib.import_module(target)
                except Exception as e:  # noqa: BLE001
                    problems.append(f"{rel}.py:{node.lineno}: from {base} import ... -> {type(e).__name__}: {e}")
                    continue
                for alias in node.names:
                    checked += 1
                    if alias.name == "*" or hasattr(mod, alias.name):
                        continue
                    try:
                        importlib.import_module(f"{target}.{alias.name}")
                    except ImportError:
                        problems.append(f"{rel}.py:{node.lineno}: {target} has no {alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    checked += 1
                    if importlib.util.find_spec(alias.name.split(".")[0]) is None:
                        problems.append(f"{rel}.py:{node.lineno}: no module {alias.name}")

assert not problems, "\n".join(problems)
print(f"imports ok: {checked} names in {', '.join(PACKAGES)}")

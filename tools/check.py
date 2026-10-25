"""Offline repository checks. Run from any directory with Python 3.12+ on Windows."""
import ast
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source_checks() -> list[str]:
    """Check maintained files without traversing local runtime or Git data."""
    errors: list[str] = []
    files = [ROOT / name for name in (
        "AGENTS.md", "README.md", "pyproject.toml", ".gitignore",
        ".editorconfig", ".gitattributes",
    )]
    for directory in ("omp_core", "tests", "tools", "docs", "examples", ".github"):
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    for path in sorted(files):
        relative = path.relative_to(ROOT)
        if not path.exists():
            errors.append(f"Missing required file: {relative}")
            continue
        if path.suffix not in (".py", ".md", ".json", ".toml", ".yml") and path.name not in (
            ".gitignore", ".editorconfig", ".gitattributes",
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8")
            # Preserve legacy SRS Markdown hard breaks, reject other trailing whitespace.
            for number, line in enumerate(content.splitlines(), 1):
                if line != line.rstrip() and not (
                    path.suffix == ".md" and line.endswith("  ")
                    and not line.endswith("   ") and not line.endswith("\t  ")
                ):
                    errors.append(f"{relative}:{number}: trailing whitespace")
            if path.suffix == ".json":
                json.loads(content)
            if path.suffix == ".py":
                tree = ast.parse(content, filename=str(relative))
                if path.is_relative_to(ROOT / "omp_core"):
                    for node in ast.walk(tree):
                        if not isinstance(node, ast.Call):
                            continue
                        if any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant)
                               and keyword.value.value is True for keyword in node.keywords):
                            errors.append(f"{relative}:{node.lineno}: shell=True is prohibited")
                        if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
                            errors.append(f"{relative}:{node.lineno}: dynamic code execution is prohibited")
        except (ValueError, SyntaxError, UnicodeError, OSError) as exc:
            errors.append(f"{relative}: {exc}")
    return errors


def main() -> int:
    errors = source_checks()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    count = suite.countTestCases()
    if count == 0:
        print("FAIL: no tests discovered", file=sys.stderr)
        return 1
    print(f"Source checks passed; running {count} tests", flush=True)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

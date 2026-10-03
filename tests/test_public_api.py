"""The public surface is `warranted` (task layer) and `warranted.host` (host kit)."""

import ast
import pkgutil
from pathlib import Path

import pytest

import warranted
import warranted.host

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = {"warranted", "warranted.host"}


def test_every_other_module_is_private():
    names = {m.name for m in pkgutil.iter_modules(warranted.__path__)}
    assert {n for n in names if not n.startswith("_")} == {"host"}


@pytest.mark.parametrize("module", [warranted, warranted.host])
def test_exported_names_resolve_and_are_public(module):
    assert len(set(module.__all__)) == len(module.__all__)
    for name in module.__all__:
        assert not name.startswith("_")
        assert getattr(module, name) is not None


def imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "warranted"
        ):
            yield node.module, [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("warranted"):
                    yield alias.name, []


CONSUMERS = sorted(
    p for p in (ROOT / "examples/m7").rglob("*.py") if "__pycache__" not in p.parts
) + [ROOT / "src/warranted/_cli.py"]


@pytest.mark.parametrize("path", CONSUMERS, ids=lambda p: str(p.relative_to(ROOT)))
def test_the_cli_and_m7_examples_use_only_the_public_api(path):
    for module, names in imports(path):
        assert module in PUBLIC, f"{path.name} imports {module}"
        for name in names:
            if module == "warranted" and name == "__version__":
                continue
            assert not name.startswith("_"), f"{path.name} imports {module}.{name}"
            target = warranted if module == "warranted" else warranted.host
            assert name in target.__all__, f"{name} is not exported by {module}"


def test_importing_the_package_prints_nothing():
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "MSWEA_SILENT_STARTUP"}
    result = subprocess.run(
        [sys.executable, "-c", "import warranted, warranted.host"],
        capture_output=True,
        env=env,
        check=True,
    )
    assert (result.stdout, result.stderr) == (b"", b"")

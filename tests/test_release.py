"""Release discipline: the API reference, changelog, and version agree with the code."""

import os
import re
import tomllib
from pathlib import Path

import warranted

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "docs/reference/api.md").read_text()
CHANGELOG = (ROOT / "CHANGELOG.md").read_text()
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_the_api_reference_documents_exactly_the_public_names():
    # Each public name has one table row whose first cell is the name in backticks.
    documented = re.findall(r"^\| `([A-Za-z_][A-Za-z0-9_]*)` \|", API, re.MULTILINE)
    assert len(documented) == len(set(documented)), "a name is documented twice"
    assert set(documented) == set(warranted.__all__)


def test_the_changelog_has_an_entry_for_the_current_version():
    versions = re.findall(r"^## \[([^\]]+)\]", CHANGELOG, re.MULTILINE)
    released = [v for v in versions if v != "Unreleased"]
    assert released and released[0] == VERSION
    assert warranted.__version__ == VERSION


def test_a_release_tag_names_the_package_version():
    # CI runs on tag pushes; a tag that disagrees with the package fails there.
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        assert os.environ["GITHUB_REF_NAME"] == f"v{VERSION}"

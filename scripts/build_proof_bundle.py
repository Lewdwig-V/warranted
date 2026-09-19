"""Build trusted tools only. Generated solutions never enter the build context."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from time import perf_counter_ns

from warranted.proofs import RESOURCES, policy_digest


def build(root: Path) -> Path:
    started = perf_counter_ns()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    pins = json.loads((RESOURCES / "toolchain.json").read_bytes())
    context = root / "context"
    context.mkdir(exist_ok=True)
    for name in ("lean", "go", "comparator", "exporter", "landrun"):
        pin = pins[name]
        archive = root / (name + (".tar.zst" if name == "lean" else ".tar.gz"))
        if not archive.exists():
            print(f"Downloading pinned {name}", flush=True)
            urllib.request.urlretrieve(pin["url"], archive)
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != pin["sha256"]:
                raise ValueError(f"{name} archive digest mismatch")
        destination = context / name
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir()
        if name == "lean":
            subprocess.run(
                [
                    "tar",
                    "--zstd",
                    "-xf",
                    str(archive),
                    "--strip-components=1",
                    "-C",
                    str(destination),
                ],
                check=True,
            )
        else:
            with tarfile.open(archive) as tar:
                tar.extractall(destination, filter="data")
            extracted = next(destination.iterdir())
            for path in extracted.iterdir():
                shutil.move(path, destination / path.name)
            extracted.rmdir()
    comparator = context / "comparator"
    # Replace only the upstream CLI entry point, preserving its checking functions.
    driver = comparator / "Main.lean"
    original = driver.read_text()
    marker = "\ndef main (args : List String) : IO Unit := do\n"
    assert original.count(marker) == 1
    driver.write_text(
        original.split(marker)[0] + "\n" + (RESOURCES / "Verify.lean").read_text()
    )
    # Use the checked local exporter archive, never an upstream floating revision.
    lakefile = comparator / "lakefile.toml"
    text = lakefile.read_text().split("[[require]]")[0]
    lakefile.write_text(
        text
        + '[[require]]\nname = "lean4export"\npath = ".lake/packages/lean4export"\n'
    )
    (comparator / "lake-manifest.json").unlink()
    packages = comparator / ".lake/packages"
    packages.mkdir(parents=True)
    shutil.move(context / "exporter", packages / "lean4export")
    shutil.copytree(RESOURCES, context / "proof", dirs_exist_ok=True)
    shutil.copyfile(RESOURCES / "Containerfile", context / "Containerfile")
    image_file = root / "image-id"
    subprocess.run(
        [
            "podman",
            "build",
            "--network=host",
            "--timestamp=0",
            "--iidfile",
            str(image_file.resolve()),
            "--build-arg",
            "BASE=" + pins["base"],
            "-f",
            str(context / "Containerfile"),
            str(context),
        ],
        check=True,
    )
    image = image_file.read_text().strip()
    manifest = subprocess.check_output(
        [
            "podman",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--http-proxy=false",
            image,
            "cat",
            "/opt/manifest.json",
        ]
    )
    bundle = {
        "policy": policy_digest(),
        "image": image,
        "toolchain": pins,
        "manifest": json.loads(manifest),
        "build_elapsed_ns": perf_counter_ns() - started,
    }
    path = root / "bundle.json"
    path.write_text(json.dumps(bundle, sort_keys=True, indent=2) + "\n")
    print(path, flush=True)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    build(parser.parse_args().destination)

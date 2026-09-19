"""Help/version entry point; the evidence ledger currently uses a Python API."""

import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="warranted",
        description="Durable, checkable knowledge for long-horizon agent work.",
        epilog=(
            "The CLI provides help/version only. The local ledger and acceptance "
            "boundary use Python APIs. Worker isolation and replay remain planned. "
            "See docs/roadmap.md."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('warranted')}"
    )
    parser.parse_args()
    parser.print_help()

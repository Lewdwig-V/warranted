"""Package entry point; domain operations arrive in later milestones."""

import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="warranted",
        description="Durable, checkable knowledge for long-horizon agent work.",
        epilog=(
            "This is the project scaffold. The evidence ledger, gates, workers, "
            "and replay engine are not implemented yet. See docs/roadmap.md."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('warranted')}"
    )
    parser.parse_args()
    parser.print_help()

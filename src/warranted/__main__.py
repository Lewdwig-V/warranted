"""Allow python -m warranted to use the same installed CLI."""

from warranted._cli import main

if __name__ == "__main__":
    raise SystemExit(main())

"""Backwards-compatible entry point. Cron runs `python -m doch1.main`.

All logic now lives in doch1.cli (Typer) and doch1.api. With no arguments the
CLI defaults to the `today` command, so the existing cron line keeps working.
"""

from doch1.cli import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()

"""Package entry point so ``python -m bot <command>`` works (delegates to the CLI).

Without this, ``python -m bot serve`` fails with "No module named bot.__main__".
``python -m bot.cli <command>`` also works and is equivalent.
"""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

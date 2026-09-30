#!/usr/bin/env python3
"""Repository root entry point: ``python main.py`` (same usage as before)."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.main import main as _main

    _main()


if __name__ == "__main__":
    main()

"""Convenience launcher: ``python main.py <url> [options]``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from web_download.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

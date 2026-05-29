"""Per-session logging: one file per bot launch, plus structured console output.

File log captures DEBUG (every WS frame, every decision); console keeps INFO.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(username: str, *, logs_dir: Path | None = None) -> Path:
    """Configure logging for a bot session.

    Writes to `<logs_dir>/<username>-<YYYYMMDD-HHMMSS>.log` at DEBUG level.
    Console gets INFO+ on stderr.

    Returns the log-file path so callers can print it.
    """
    logs_dir = logs_dir or Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_user = "".join(c if c.isalnum() or c in "-_" else "_" for c in username) or "bot"
    log_path = logs_dir / f"{safe_user}-{timestamp}.log"

    root = logging.getLogger("hanabi_bot")
    root.setLevel(logging.DEBUG)
    # Clear any pre-existing handlers (so repeat setup_logging calls don't duplicate).
    for h in list(root.handlers):
        root.removeHandler(h)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-5s %(message)s"))
    root.addHandler(ch)

    root.info("session log: %s", log_path)
    return log_path

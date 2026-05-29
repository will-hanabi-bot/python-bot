"""Refresh vendored variants.json and suits.json from the canonical hanabi-live repo.

Run with: uv run python scripts/update_variants.py

Idempotent: only writes if the upstream content has changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

UPSTREAM = "https://raw.githubusercontent.com/Hanabi-Live/hanabi-live/main/packages/game/src/json"
TARGETS = {
    "variants.json": "variants.json",
    "suits.json": "suits.json",
}


def main() -> int:
    data_dir = Path(__file__).resolve().parent.parent / "src" / "hanabi_bot" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    changed = 0
    for upstream_name, local_name in TARGETS.items():
        url = f"{UPSTREAM}/{upstream_name}"
        local = data_dir / local_name
        print(f"Fetching {url} ... ", end="", flush=True)
        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        new = resp.content
        old = local.read_bytes() if local.exists() else b""
        if new == old:
            print("unchanged")
            continue
        local.write_bytes(new)
        print(f"updated ({len(new)} bytes)")
        changed += 1

    print(f"\nDone. {changed} file(s) updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

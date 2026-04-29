"""Discord webhook notifier. Called from run_generate.bat on failure."""
from __future__ import annotations

import os
import sys

import requests


def notify_discord(message: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        requests.post(url, json={"content": message}, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "av-actress-blog: unknown error"
    notify_discord(msg)

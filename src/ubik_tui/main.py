"""UBIK-TUI entry point."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

DEFAULT_ENGINE_URL = "http://localhost:8801"
DEFAULT_SSH_HOST = "dev-station-02"


def _engine_reachable(url: str, timeout: float = 2.0) -> bool:
    import httpx
    try:
        return httpx.get(f"{url}/health", timeout=timeout).status_code == 200
    except Exception:
        return False


def _ensure_tunnel(url: str, ssh_host: str) -> bool:
    if _engine_reachable(url):
        return True
    print(f"Opening SSH tunnel to {ssh_host}…", end="", flush=True)
    proc = subprocess.Popen(
        ["ssh", "-N", "-q", "-L", "8801:localhost:8801",
         "-o", "StrictHostKeyChecking=no", "-o", "ExitOnForwardFailure=yes", ssh_host],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(16):
        time.sleep(0.5)
        if _engine_reachable(url):
            print(" OK")
            return True
    proc.terminate()
    print(" FAILED")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="UBIK-TUI — full-screen terminal UI")
    parser.add_argument("--url", default=os.environ.get("UBIK_URL", DEFAULT_ENGINE_URL))
    parser.add_argument("--api-key", default=os.environ.get("UBIK_API_KEY", "local"))
    parser.add_argument("--user", default=os.environ.get("UBIK_USER", "damien"))
    parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
    args = parser.parse_args()

    _ensure_tunnel(args.url, args.ssh_host)

    from ubik import Ubik
    agent = Ubik(api_url=args.url, api_key=args.api_key, user_id=args.user)

    from ubik_tui.app import UbikTUI
    app = UbikTUI(agent=agent, engine_url=args.url)
    app.run()


if __name__ == "__main__":
    main()

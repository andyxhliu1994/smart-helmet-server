from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HEALTH_URL = "http://127.0.0.1:18787/api/health"
DEFAULT_LOG = ROOT / "service_watch.log"
MAIN_SERVICE = ROOT / "tools" / "run_main_service.py"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_log(path: Path, message: str) -> None:
    line = f"{now()} {message}"
    print(line, flush=True)
    with path.open("a", encoding="utf-8", buffering=1) as file:
        file.write(line + "\n")


def check_health(url: str, timeout: float) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=timeout) as response:
            body = response.read(512)
            status = getattr(response, "status", 0)
        if status != 200:
            return False, f"HTTP {status}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return True, "HTTP 200"
        if payload.get("ok") is True:
            return True, "healthy"
        return False, f"unexpected payload: {payload!r}"
    except TimeoutError:
        return False, "timeout"
    except URLError as exc:
        return False, str(exc.reason)
    except OSError as exc:
        return False, str(exc)


def start_main_service(log_path: Path) -> None:
    if not MAIN_SERVICE.exists():
        raise FileNotFoundError(MAIN_SERVICE)

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    subprocess.Popen(
        [sys.executable, str(MAIN_SERVICE)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    write_log(log_path, f"started main service: {sys.executable} {MAIN_SERVICE}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch the Agent main service health endpoint.")
    parser.add_argument("--url", default=DEFAULT_HEALTH_URL, help="Health URL to check.")
    parser.add_argument("--interval", type=float, default=15.0, help="Seconds between checks.")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds.")
    parser.add_argument("--fail-threshold", type=int, default=2, help="Failures before reporting unhealthy.")
    parser.add_argument("--restart", action="store_true", help="Start the main service after repeated failures.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="Monitor log path.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.log = args.log.resolve()
    args.log.parent.mkdir(parents=True, exist_ok=True)

    write_log(args.log, f"watcher started url={args.url} interval={args.interval}s restart={args.restart}")
    failures = 0
    last_state: bool | None = None

    while True:
        healthy, detail = check_health(args.url, args.timeout)
        if healthy:
            failures = 0
            if last_state is not True:
                write_log(args.log, f"status=healthy detail={detail}")
            last_state = True
        else:
            failures += 1
            write_log(args.log, f"status=failed failures={failures} detail={detail}")
            if failures >= max(1, args.fail_threshold):
                if last_state is not False:
                    write_log(args.log, "status=unhealthy")
                last_state = False
                if args.restart:
                    start_main_service(args.log)
                    failures = 0

        if args.once:
            return 0 if healthy else 1
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())

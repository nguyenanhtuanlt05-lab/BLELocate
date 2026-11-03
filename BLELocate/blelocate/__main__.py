"""Command line: ``hub`` (P1), ``anchor`` (P2/P3) and ``selftest``."""

from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import logging.handlers
import sys
from pathlib import Path

from . import APP_NAME, DEFAULT_DISCOVERY_PORT, DEFAULT_HTTP_PORT, __version__
from .anchor import AnchorStopped
from .config import ANCHOR_IDS, default_data_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blelocate", description=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command")

    hub = sub.add_parser("hub", help="run P1: dashboard host and first anchor")
    hub.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port (default %(default)s)")
    hub.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT, help="UDP discovery port")
    hub.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all)")
    hub.add_argument("--data-dir", type=Path, default=None, help="where config.json and logs live")
    hub.add_argument("--no-scan", action="store_true", help="do not use P1's own Bluetooth adapter")
    hub.add_argument("--no-browser", action="store_true", help="do not open the dashboard")

    anchor = sub.add_parser("anchor", help="run a laptop anchor (P2 or P3)")
    anchor.add_argument("--hub", help="P1 address, e.g. 192.168.1.10 or 192.168.1.10:8780 (default: find it)")
    anchor.add_argument("--anchor", type=str.upper, choices=ANCHOR_IDS,
                        help="which anchor this laptop is (default: next free)")
    anchor.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    anchor.add_argument("--data-dir", type=Path, default=None, help="where logs are written")
    anchor.add_argument("--no-browser", action="store_true", help="do not open P1's dashboard")

    test = sub.add_parser("selftest", help="check Bluetooth support files and the adapter")
    test.add_argument("--require-windows-scanner", action="store_true", help="fail unless the WinRT scanner loads")
    test.add_argument("--no-adapter", action="store_true", help="skip the Bluetooth adapter check")
    test.add_argument("--scan", type=float, default=0, metavar="SECONDS", help="also scan for this many seconds")
    test.add_argument("--target", help="service UUID to look for during --scan")
    return parser


def setup_logging(data_dir: Path, name: str) -> Path | None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path = None
    try:
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
        log_path = data_dir / "logs" / f"{name}.log"
        handlers.append(logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", handlers=handlers)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    return log_path


def ask_command() -> list[str]:
    """Double-clicking the exe gives a small menu instead of a usage error."""
    print(f"{APP_NAME} {__version__}\n  1  Start P1 (dashboard host + anchor)\n  2  Start an anchor (P2/P3)\n  3  Run self-test")
    choice = input("Choose 1, 2 or 3: ").strip()
    if choice == "1":
        return ["hub"]
    if choice == "2":
        role = input("Which anchor is this (P2/P3, Enter = next free)? ").strip().upper()
        return ["anchor"] + (["--anchor", role] if role in ANCHOR_IDS else [])
    return ["selftest"]


async def run_hub(args) -> None:
    from .config import ConfigStore
    from .hub import Hub

    store = ConfigStore(args.data_dir / "config.json")
    hub = Hub(store, port=args.port, discovery_port=args.discovery_port, bind=args.bind,
              local_anchor=not args.no_scan, open_browser=not args.no_browser)
    await hub.start()
    info = hub.info()
    log = logging.getLogger("blelocate")
    log.info("%s %s is running (hub id %s).", APP_NAME, __version__, hub.hub_id)
    log.info("Dashboard on this laptop: http://localhost:%s/", hub.port)
    for url in info["dashboard_urls"]:
        log.info("Dashboard for other devices: %s", url)
    if not info["dashboard_urls"]:
        log.warning("No LAN address found. Connect P1 to the same Wi-Fi as the anchors.")
    log.info("Shared configuration: %s", store.path)
    log.info("Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        await hub.stop()


async def run_anchor(args) -> None:
    from .anchor import AnchorAgent

    agent = AnchorAgent(args.hub, args.anchor, discovery_port=args.discovery_port, open_dashboard=not args.no_browser)
    await agent.run()


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    if not argv and sys.stdin and sys.stdin.isatty():
        argv = ask_command()
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
        return 2
    if args.command == "selftest":
        from .selftest import run_selftest

        return run_selftest(args.require_windows_scanner, not args.no_adapter, args.scan, args.target)

    args.data_dir = args.data_dir or default_data_dir()
    log_path = setup_logging(args.data_dir, args.command)
    if log_path:
        logging.getLogger("blelocate").info("Log file: %s", log_path)
    try:
        asyncio.run(run_hub(args) if args.command == "hub" else run_anchor(args))
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            logging.error("Port %s is already in use. Is BLE Locate already running on this computer? "
                          "Close the other window, or start with --port <another port> "
                          "(then open the firewall for that port too).", args.port)
            return 2
        raise
    except AnchorStopped as exc:
        logging.error("%s", exc)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

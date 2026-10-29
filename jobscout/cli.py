from __future__ import annotations

import argparse
import sys

from jobscout.browser import browser_context
from jobscout.config import load_settings
from jobscout.events import EventBus
from jobscout.run_manager import RunManager


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobscout")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run a job search")
    run_parser.add_argument(
        "--sources",
        choices=["both", "linkedin", "indeed"],
        default="both",
    )
    run_parser.add_argument("--cached", action="store_true", help="Use cached fixtures")

    serve_parser = sub.add_parser("serve", help="Serve the browser UI")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    warm_parser = sub.add_parser("warm", help="Open a source browser for manual login")
    warm_parser.add_argument("source", choices=["linkedin", "indeed"])

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args.host, args.port)
    if args.command == "warm":
        return _warm(args.source)
    if args.command == "run":
        return _run(args.sources, args.cached)
    parser.print_help()
    return 0


def _sources(value: str) -> list[str]:
    return ["linkedin", "indeed"] if value == "both" else [value]


def _run(source_arg: str, cached: bool) -> int:
    settings = load_settings()
    bus = EventBus()
    manager = RunManager(settings, bus)

    def on_event(event: dict) -> None:
        event_type = event.get("type")
        if event_type == "message":
            print(
                f"{event.get('from'):>10} → {event.get('to'):<10} "
                f"[{event.get('kind')}] {event.get('text')}",
                flush=True,
            )
        elif event_type in {"run.started", "run.paused", "run.resumed", "run.finished"}:
            print(f"[{event_type}]", flush=True)
        elif event_type == "run.failed":
            print(f"[run.failed] {event.get('error')}", flush=True)
        elif event_type == "run.cancelled":
            print("[run.cancelled]", flush=True)
        elif event_type == "log":
            print(f"[log] {event.get('text')}", flush=True)

    bus.add_listener(on_event)
    manager.start(_sources(source_arg), cached=cached)
    manager.wait()
    state = manager.state
    print(
        f"\nstatus={state.status} found={state.stats.found} "
        f"new={state.stats.new} skipped={state.stats.skipped} "
        f"errors={state.stats.errors} sink={state.sink_kind}",
        flush=True,
    )
    if state.sheet_url:
        print(state.sheet_url, flush=True)
    return 0 if state.status.value == "completed" else 1


def _serve(host: str | None, port: int | None) -> int:
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "jobscout.ui.server:app",
        host=host or settings.ui.host,
        port=port or settings.ui.port,
        reload=False,
    )
    return 0


def _warm(source: str) -> int:
    import time

    settings = load_settings()
    settings.browser.headless = False
    url = (
        "https://www.linkedin.com/login"
        if source == "linkedin"
        else "https://de.indeed.com/"
    )
    print(f"Opening {source}. Sign in, accept cookies, or solve the verification challenge.")
    print("Do not close the browser until the page shows normal search results.")
    print("Leave this terminal open; press Enter here when you are done.")
    with browser_context(settings, source) as (context, page):
        page.bring_to_front()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if sys.stdin.isatty():
            try:
                input("Press Enter here when you are done...")
            except (KeyboardInterrupt, EOFError):
                pass
        else:
            print("No interactive terminal detected. Close the browser when done.")
            try:
                while context.pages:
                    time.sleep(1.0)
            except Exception:
                pass
        try:
            title = page.title()
            current_url = page.url
            print(f"Final page: {title} · {current_url}")
            if source == "indeed" and any(
                marker in title.lower()
                for marker in ("just a moment", "blocked", "verification")
            ):
                print(
                    "Indeed is still showing a challenge. Complete it in the browser, "
                    "then run the warm command again."
                )
            cookies = context.cookies()
            print(f"Cookies saved in profile '{source}': {len(cookies)}")
            if source == "linkedin":
                print(
                    "LinkedIn session cookie:",
                    "found" if any(c.get("name") == "li_at" for c in cookies) else "NOT found",
                )
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

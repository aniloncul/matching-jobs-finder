from __future__ import annotations

import argparse
import json
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
    warm_parser.add_argument(
        "--clean",
        "--reset",
        action="store_true",
        help="Clear saved cookies/profile before opening browser",
    )

    ats_parser = sub.add_parser("ats", help="Deterministic ATS checks for a PDF")
    ats_sub = ats_parser.add_subparsers(dest="ats_command")
    ats_check = ats_sub.add_parser("check", help="Score a PDF's text layer and layout")
    ats_check.add_argument("pdf", help="Path to the PDF")
    ats_check.add_argument("--pages", type=int, default=None, help="Expected page count")
    ats_check.add_argument("--min-score", type=int, default=70, help="Pass threshold (default 70)")
    ats_check.add_argument("--json", action="store_true", help="Machine-readable output")

    live_parser = sub.add_parser("liveness", help="Probe saved jobs for expiry")
    live_sub = live_parser.add_subparsers(dest="liveness_command")
    live_check = live_sub.add_parser("check", help="Check saved jobs and persist status")
    live_check.add_argument("--limit", type=int, default=None, help="Max jobs to check")
    live_check.add_argument(
        "--all",
        dest="force",
        action="store_true",
        help="Re-check jobs already marked expired",
    )
    live_check.add_argument("--json", action="store_true", help="Machine-readable output")

    sweep_parser = sub.add_parser("sweep", help="Apply the deadline sweep to saved jobs")
    sweep_parser.add_argument(
        "--closing-days",
        type=int,
        default=7,
        help="Days before the deadline to flag closing_soon (default 7)",
    )
    sweep_parser.add_argument("--json", action="store_true", help="Machine-readable output")

    rank_parser = sub.add_parser("rank", help="Score fit for saved jobs")
    rank_parser.add_argument("--limit", type=int, default=None, help="Max jobs to score")
    rank_parser.add_argument("--min-score", type=int, default=None, help="Verdict threshold")
    rank_parser.add_argument("--json", action="store_true", help="Machine-readable output")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args.host, args.port)
    if args.command == "warm":
        return _warm(args.source, clean=args.clean)
    if args.command == "run":
        return _run(args.sources, args.cached)
    if args.command == "ats":
        return _ats(args)
    if args.command == "liveness":
        return _liveness(args)
    if args.command == "sweep":
        return _sweep(args)
    if args.command == "rank":
        return _rank(args)
    parser.print_help()
    return 0


def _ats(args: argparse.Namespace) -> int:
    from jobscout.verification import PdfReadError, analyze_pdf, check_pdf
    from jobscout.verification.ats import format_report

    if getattr(args, "ats_command", None) != "check":
        print("usage: jobscout ats check <pdf>", file=sys.stderr)
        return 2
    try:
        report = analyze_pdf(
            args.pdf,
            expected_pages=args.pages,
            min_score=args.min_score,
        )
        pdf_check = check_pdf(args.pdf, expected_pages=args.pages)
    except PdfReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {"ats": report.to_dict(), "pdf": pdf_check.to_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_report(report))
        if pdf_check.issues:
            print("\nText-layer issues:")
            for issue in pdf_check.issues:
                print(f"- [{issue.severity}] {issue.message}")
    return 0 if report.passed and pdf_check.ok else 1


def _sources(value: str) -> list[str]:
    return ["linkedin", "indeed"] if value == "both" else [value]


def _load_jobs(limit: int | None = None):
    """Read saved jobs from the configured sink."""
    from jobscout.sinks import build_sink

    settings = load_settings()
    sink = build_sink(settings)
    sink.prepare()
    jobs = sink.list_jobs(limit=limit if limit is not None else 100_000)
    return settings, sink, jobs


def _liveness(args: argparse.Namespace) -> int:
    if getattr(args, "liveness_command", None) != "check":
        print("usage: jobscout liveness check [--limit N] [--all]", file=sys.stderr)
        return 2

    from jobscout.liveness import (
        check_liveness,
        make_client,
        store_for as liveness_store_for,
    )

    settings, sink, jobs = _load_jobs(args.limit)
    if not jobs:
        print("No saved jobs to check.")
        return 0

    store = liveness_store_for(settings)
    counts: dict[str, int] = {}
    results = []
    with make_client(settings) as client:
        for job in jobs:
            result = check_liveness(
                job,
                settings=settings,
                client=client,
                store=store,
                force=args.force,
            )
            counts[result.status] = counts.get(result.status, 0) + 1
            results.append(
                {
                    "source": job.source.value,
                    "job_id": job.job_id,
                    "company": job.company,
                    "title": job.title,
                    **result.to_dict(),
                }
            )
            sink.update_job_fields(
                job.source.value,
                job.job_id,
                {
                    "Status": job.status,
                },
            )

    if args.json:
        print(json.dumps({"counts": counts, "results": results}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(
                f"[{item['status']:>9}] {item['company']} · {item['title']} "
                f"({item['reason']})"
            )
        print(
            f"\nactive={counts.get('active', 0)} "
            f"expired={counts.get('expired', 0)} "
            f"uncertain={counts.get('uncertain', 0)}"
        )
    return 0


def _sweep(args: argparse.Namespace) -> int:
    from jobscout.deadlines import sweep_saved_jobs

    settings, sink, jobs = _load_jobs()
    if not jobs:
        print("No saved jobs to sweep.")
        return 0

    report = sweep_saved_jobs(jobs, closing_days=args.closing_days)
    for job in jobs:
        sink.update_job_fields(
            job.source.value,
            job.job_id,
            {
                "Deadline": job.deadline or "",
                "Deadline Status": job.deadline_status,
                "Status": job.status,
            },
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report:
            if item["deadline_status"] == "unknown":
                continue
            print(
                f"[{item['deadline_status']:>11}] {item['company']} · {item['title']} "
                f"→ {item['deadline'] or 'n/a'}"
            )
    return 0


def _rank(args: argparse.Namespace) -> int:
    from jobscout.scoring import (
        compute_fit,
        format_ranking,
        parse_fit_payload,
        passing,
        rank_jobs,
        store_for as ranking_store_for,
    )

    settings, sink, jobs = _load_jobs(args.limit)
    if not jobs:
        print("No saved jobs to rank.")
        return 0

    min_score = args.min_score if args.min_score is not None else settings.fit.min_score

    from jobscout.cover_letter.llm import CoverLetterLLM
    from jobscout.cover_letter.profile import load_profile

    config = settings.cover_letter
    client = CoverLetterLLM(
        base_url=config.base_url,
        api_key=config.api_key or "ollama",
        model=config.model,
        temperature=0.0,
        max_tokens=700,
        timeout_seconds=config.timeout_seconds,
        num_ctx=getattr(config, "num_ctx", 4096),
    )
    profile_text = ""
    candidate_location = None
    try:
        profile = load_profile(config.profile_path)
        profile_text = profile.prompt_text()
        candidate_location = getattr(profile, "city", None) or None
    except Exception:
        pass

    store = ranking_store_for(settings)
    pairs = []
    for job in jobs:
        description = job.description or job.posted_text or ""
        raw = client.score_fit(
            profile_text,
            job,
            description,
            candidate_location=candidate_location,
        )
        fit = compute_fit(parse_fit_payload(raw), deadline=job.deadline)
        pairs.append((job, fit))
        sink.update_job_fields(
            job.source.value,
            job.job_id,
            {
                "Rank Score": str(fit.score),
                "Rank Verdict": fit.verdict,
            },
        )

    ordered = rank_jobs(pairs)
    for job, fit in ordered:
        store.upsert(job, fit)

    kept = passing(ordered, min_score)
    if args.json:
        print(
            json.dumps(
                {
                    "kept": [job.job_id for job in kept],
                    "results": [
                        {"job": job.job_id, **fit.to_dict()} for job, fit in ordered
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for job, fit in ordered:
            print(format_ranking(job, fit))
        print(f"\n{len(kept)}/{len(ordered)} jobs at or above {min_score}")
    return 0


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


def _warm(source: str, clean: bool = False) -> int:
    import shutil
    import time
    from pathlib import Path

    settings = load_settings()
    settings.browser.headless = False

    if clean:
        profile_dir = Path(settings.browser.profile_dir) / source
        if profile_dir.exists():
            print(f"🧹 Clearing cookies & profile directory: {profile_dir}")
            shutil.rmtree(profile_dir, ignore_errors=True)
            print("✨ Profile cleared.")

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

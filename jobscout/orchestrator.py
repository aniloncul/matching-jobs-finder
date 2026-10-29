from __future__ import annotations

import time
from collections.abc import Callable

from jobscout.agents.roles import Coordinator, Registrar, Scout
from jobscout.config import Settings
from jobscout.control import RunCancelled, RunControl
from jobscout.cover_letter.description import fetch_job_description
from jobscout.cover_letter.description_store import (
    load_saved_description,
    save_job_description,
)
from jobscout.dedupe import dedupe_jobs
from jobscout.models import Job
from jobscout.sinks import Sink, build_sink
from jobscout.sources import make_source


Emit = Callable[..., None]


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        control: RunControl,
        emit: Emit,
        sources: list[str],
        cached: bool,
        run_id: str,
        mode: str,
        started_at: float,
    ) -> None:
        self.settings = settings
        self.control = control
        self.emit = emit
        self.sources = sources
        self.cached = cached
        self.run_id = run_id
        self.mode = mode
        self.started_at = started_at

    def run(self) -> None:
        coordinator = Coordinator(self.emit)
        registrar = Registrar(self.emit)
        sink: Sink | None = None
        completed: dict[str, list[Job]] = {}
        wrote = False
        errors = 0

        self.emit("stats", found=0, new=0, skipped=0, errors=0)
        coordinator.status("working", "Preparing run")
        coordinator.log("Starting run preparation")
        coordinator.send("registrar", "command", "Prepare Jobs and Runs tabs")
        registrar.status("working", "Preparing sink")
        registrar.log("Initializing sink")

        try:
            self.control.checkpoint("prepare sink")
            sink = build_sink(
                self.settings,
                log=lambda message: self.emit(
                    "log", agent="registrar", level="warn", text=message
                ),
            )
            self.emit(
                "sink.ready",
                kind=sink.kind,
                sheet_url=sink.sheet_url,
            )
            coordinator.send("registrar", "status", "Sink ready")
            registrar.announce_ready()
            existing_keys, existing_fingerprints = sink.existing()
            self.control.checkpoint("sink scanned")
            coordinator.log("Sink scanned, ready for results")

            coordinator.status("working", "Coordinating source searches")
            for name in self.sources:
                self.control.checkpoint(f"{name} dispatch")
                cfg = self.settings.source_config(name)
                scout = Scout(name, self.emit)
                coordinator.command(
                    name,
                    f"{cfg.query} · {cfg.location} · newest first",
                )
                coordinator.log(f"Dispatching {name} search")
                scout.status("working", f"Opening {name.title()} search", 0.02)
                scout.log(f"Opening {name.title()} search page")

                try:
                    source = make_source(
                        name,
                        self.settings,
                        self.control,
                        self.emit,
                        self.cached,
                    )
                    jobs = source.search()
                    completed[name] = jobs
                    scout.status("done", f"{len(jobs)} jobs extracted", 1.0)
                    scout.result(len(jobs))
                    scout.log(f"Extracted {len(jobs)} jobs from {name}")
                    registrar.receiving(name, len(jobs))
                    registrar.log(f"Received {len(jobs)} jobs from {name}")
                    registrar.ready()
                except RunCancelled:
                    raise
                except Exception as exc:
                    errors += 1
                    scout.failed(str(exc))
                    scout.log(f"Error during {name} search: {str(exc)[:120]}", level="error")
                    coordinator.status("working", f"Continuing after {name} error")
                    coordinator.log(f"Continuing after {name} error")

                found = sum(len(items) for items in completed.values())
                self.emit("stats", found=found, new=0, skipped=0, errors=errors)
                self.control.checkpoint(f"{name} complete")

            all_jobs = [job for jobs in completed.values() for job in jobs]
            coordinator.status("working", "Merging source results")
            coordinator.log(f"Merging {len(all_jobs)} jobs from sources")
            registrar.status("working", "Deduplicating results", 0.35)
            registrar.log("Deduplicating results")
            self.control.checkpoint("dedupe")

            unique, _duplicates = dedupe_jobs(
                all_jobs,
                existing_keys=existing_keys,
                existing_fingerprints=existing_fingerprints,
                cross_source=self.settings.dedupe_cross_source,
            )
            self.emit(
                "stats",
                found=len(all_jobs),
                new=0,
                skipped=len(all_jobs) - len(unique),
                errors=errors,
            )
            coordinator.log(f"Deduplication complete: {len(unique)} unique, {len(all_jobs) - len(unique)} duplicates")
            registrar.log(f"Deduplication complete: {len(unique)} unique, {len(all_jobs) - len(unique)} duplicates")

            if unique:
                registrar.writing(len(unique))
                registrar.log(f"Writing {len(unique)} jobs to sink")
                new_count = sink.write(
                    unique,
                    cross_source=self.settings.dedupe_cross_source,
                )
                wrote = True
                registrar.log(f"Successfully wrote {new_count} new jobs")
            else:
                new_count = 0
                registrar.log("No new jobs to write")

            if unique and not self.cached and self.settings.cover_letter.auto_fetch_descriptions:
                self._fetch_descriptions(sink, unique, registrar)

            skipped = max(0, len(all_jobs) - new_count)
            self.emit(
                "stats",
                found=len(all_jobs),
                new=new_count,
                skipped=skipped,
                errors=errors,
            )
            registrar.finished(new_count, skipped)
            coordinator.complete(f"Run complete · {new_count} new rows")
            coordinator.log(f"Run completed successfully with {new_count} new jobs")
            self._record_run(
                sink,
                status="completed",
                found=len(all_jobs),
                new=new_count,
                skipped=skipped,
                errors=errors,
            )
            self.control.checkpoint("after write")

        except RunCancelled:
            if completed and sink is not None and not wrote:
                try:
                    all_jobs = [job for jobs in completed.values() for job in jobs]
                    unique, _duplicates = dedupe_jobs(
                        all_jobs,
                        existing_keys=existing_keys if "existing_keys" in locals() else None,
                        existing_fingerprints=(
                            existing_fingerprints
                            if "existing_fingerprints" in locals()
                            else None
                        ),
                        cross_source=self.settings.dedupe_cross_source,
                    )
                    new_count = sink.write(
                        unique,
                        cross_source=self.settings.dedupe_cross_source,
                    )
                    self.emit(
                        "stats",
                        found=len(all_jobs),
                        new=new_count,
                        skipped=max(0, len(all_jobs) - new_count),
                        errors=errors,
                    )
                    registrar.status(
                        "done",
                        f"Cancelled · preserved {new_count} completed-source rows",
                        1.0,
                    )
                    registrar.send(
                        "coordinator",
                        "status",
                        f"Cancelled · {new_count} completed-source rows preserved",
                    )
                except Exception as exc:
                    self.emit(
                        "log",
                        agent="registrar",
                        level="error",
                        text=f"Cancel save failed: {exc}",
                    )
            coordinator.log("Run cancelled by user")
            self._record_run(
                sink,
                status="cancelled",
                found=len([job for jobs in completed.values() for job in jobs]),
                new=0,
                skipped=0,
                errors=errors,
                error="Cancelled by user",
            )
            raise

        except Exception as exc:
            coordinator.log(f"Run failed: {str(exc)[:120]}", level="error")
            self._record_run(
                sink,
                status="failed",
                found=0,
                new=0,
                skipped=0,
                errors=errors,
                error=str(exc),
            )
            raise

    def _fetch_descriptions(
        self,
        sink: Sink,
        jobs: list[Job],
        registrar: Registrar,
    ) -> None:
        limit = max(0, int(self.settings.cover_letter.auto_fetch_limit))
        if limit == 0:
            return
        candidates = [
            job
            for job in jobs
            if not job.description
            and not load_saved_description(
                self.settings,
                job.source.value,
                job.job_id,
            )
        ][:limit]
        if not candidates:
            return

        registrar.status(
            "working",
            f"Fetching descriptions · 0/{len(candidates)}",
            0.90,
        )
        if hasattr(registrar, "log"):
            registrar.log(f"Fetching descriptions for {len(candidates)} jobs")
        saved = 0
        for index, job in enumerate(candidates, start=1):
            self.control.checkpoint("fetch job description")
            registrar.status(
                "working",
                f"Fetching descriptions · {index}/{len(candidates)} · {job.company}",
                0.90 + 0.08 * index / len(candidates),
            )
            try:
                text = fetch_job_description(self.settings, job)
                save_job_description(self.settings, job, text)
                sink.save_description(job.source.value, job.job_id, text)
                saved += 1
                if hasattr(registrar, "log"):
                    registrar.log(f"Saved description for {job.company} ({job.job_id})")
            except Exception as exc:
                self.emit(
                    "log",
                    agent="registrar",
                    level="warn",
                    text=(
                        f"Description skipped · {job.source.value} {job.job_id}: "
                        f"{str(exc)[:160]}"
                    ),
                )
        registrar.send(
            "coordinator",
            "status",
            f"Descriptions saved · {saved}/{len(candidates)}",
        )
        if hasattr(registrar, "log"):
            registrar.log(f"Finished fetching descriptions: {saved}/{len(candidates)} saved")

    def _record_run(
        self,
        sink: Sink | None,
        *,
        status: str,
        found: int,
        new: int,
        skipped: int,
        errors: int,
        error: str | None = None,
    ) -> None:
        if sink is None:
            return
        try:
            sink.record_run(
                {
                    "run_id": self.run_id,
                    "sources": self.sources,
                    "mode": self.mode,
                    "found": found,
                    "new": new,
                    "skipped": skipped,
                    "errors": errors,
                    "status": status,
                    "error": error or "",
                    "started_at": self.started_at,
                    "finished_at": time.time(),
                }
            )
        except Exception as exc:
            self.emit(
                "log",
                agent="registrar",
                level="warn",
                text=f"Run log write failed: {exc}",
            )

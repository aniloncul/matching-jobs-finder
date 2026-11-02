from __future__ import annotations

import time
from collections.abc import Callable

from jobscout.agents.roles import Analyst, Coordinator, Registrar, Scout, Verifier
from jobscout.config import KEYLESS_SOURCE_NAMES, Settings
from jobscout.control import RunCancelled, RunControl
from jobscout.cover_letter.description import fetch_job_description
from jobscout.cover_letter.description_store import (
    load_saved_description,
    save_job_description,
)
from jobscout.deadlines import apply_deadlines
from jobscout.dedupe import dedupe_jobs
from jobscout.exclusions import (
    get_excluded_keys_and_fingerprints,
    get_excluded_urls_and_roles,
)
from jobscout.liveness import check_batch
from jobscout.models import Job
from jobscout.scoring import (
    FitResult,
    compute_fit,
    parse_fit_payload,
    passing,
    rank_jobs,
    store_for as ranking_store_for,
)
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
        verifier = Verifier(self.emit)
        analyst = Analyst(self.emit)
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
            existing = sink.existing()
            self.control.checkpoint("sink scanned")
            coordinator.log("Sink scanned, ready for results")

            coordinator.status("working", "Coordinating source searches")
            for name in self.sources:
                self.control.checkpoint(f"{name} dispatch")
                cfg = self.settings.source_config(name)
                # The five keyless sources are one UI agent: "boards".
                agent = "boards" if name in KEYLESS_SOURCE_NAMES else name
                scout = Scout(agent, self.emit)
                coordinator.command(
                    agent,
                    f"{cfg.query} · {cfg.location} · newest first"
                    + ("" if agent == name else f" · via {name}"),
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
                    result = source.search()
                    jobs = result.jobs
                    if not result.ok:
                        errors += 1
                        reason = result.message or f"{name} · {result.status}"
                        scout.failed(reason)
                        scout.log(f"{name} search failed: {reason[:150]}", level="error")
                        coordinator.status("working", f"Continuing after {name} error")
                        coordinator.log(f"Continuing after {name} error")
                    else:
                        completed[name] = jobs
                        if jobs:
                            scout.status("done", f"{len(jobs)} jobs extracted", 1.0)
                            scout.result(len(jobs))
                            scout.log(f"Extracted {len(jobs)} jobs from {name}")
                        else:
                            scout.status("done", "No matching jobs", 1.0)
                            scout.result(0)
                            scout.log(f"No matching jobs on {name}")
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

            ex_keys, ex_fps = get_excluded_keys_and_fingerprints(self.settings)
            ex_urls, ex_roles = get_excluded_urls_and_roles(self.settings)
            warnings: list[str] = []
            unique, _duplicates = dedupe_jobs(
                all_jobs,
                existing_keys=existing.keys,
                existing_fingerprints=existing.fingerprints,
                cross_source=self.settings.dedupe_cross_source,
                excluded_keys=ex_keys,
                excluded_fingerprints=ex_fps,
                existing_urls=existing.urls,
                existing_roles=existing.roles,
                excluded_urls=ex_urls,
                excluded_roles=ex_roles,
                warnings=warnings,
            )
            for warning in warnings[:5]:
                registrar.log(warning, level="warn")
            self.emit(
                "stats",
                found=len(all_jobs),
                new=0,
                skipped=len(all_jobs) - len(unique),
                errors=errors,
            )
            coordinator.log(f"Deduplication complete: {len(unique)} unique, {len(all_jobs) - len(unique)} duplicates")
            registrar.log(f"Deduplication complete: {len(unique)} unique, {len(all_jobs) - len(unique)} duplicates")

            # --- Verifier: deadlines always, liveness only when enabled -----
            unique = self._verify(unique, verifier, coordinator, registrar)

            # --- Analyst: deterministic fit ranking, opt-in ---------------
            unique = self._analyse(unique, analyst, coordinator)

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
                    known = existing if "existing" in locals() else None
                    unique, _duplicates = dedupe_jobs(
                        all_jobs,
                        existing_keys=known.keys if known else None,
                        existing_fingerprints=known.fingerprints if known else None,
                        cross_source=self.settings.dedupe_cross_source,
                        excluded_keys=ex_keys if "ex_keys" in locals() else None,
                        excluded_fingerprints=ex_fps if "ex_fps" in locals() else None,
                        existing_urls=known.urls if known else None,
                        existing_roles=known.roles if known else None,
                        excluded_urls=ex_urls if "ex_urls" in locals() else None,
                        excluded_roles=ex_roles if "ex_roles" in locals() else None,
                    )
                    unique = [job for job in unique if job.status != "expired"]
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

    def _verify(
        self,
        jobs: list[Job],
        verifier: Verifier,
        coordinator: Coordinator,
        registrar: Registrar,
    ) -> list[Job]:
        """Deadline extraction (always) and optional liveness probing."""
        if not jobs:
            verifier.finished("Nothing to verify")
            return jobs

        self.control.checkpoint("deadline sweep")
        verifier.status("working", "Verifying postings", 0.1)
        verifier.checking_deadlines(len(jobs))
        counts = apply_deadlines(jobs)
        verifier.deadlines_done(counts)
        registrar.log(
            "Deadlines · "
            f"{counts.get('closing_soon', 0)} closing soon · "
            f"{counts.get('expired', 0)} expired · "
            f"{counts.get('unparseable', 0)} unparseable"
        )

        alive = [job for job in jobs if job.status != "expired"]
        dropped = len(jobs) - len(alive)
        if dropped:
            verifier.log(f"Removed {dropped} job(s) with a passed deadline")
            coordinator.log(f"Verifier removed {dropped} expired job(s)")

        if self.settings.liveness.enabled:
            self.control.checkpoint("liveness")
            verifier.checking_liveness(min(len(alive), self.settings.liveness.max_checks))
            liveness_counts = check_batch(alive, settings=self.settings)
            verifier.liveness_done(liveness_counts)
            alive = [job for job in alive if job.status != "expired"]
        else:
            verifier.log("Liveness probing disabled (JOBSCOUT_LIVENESS_ENABLED=0)")

        verifier.finished(f"{len(alive)} job(s) verified")
        return alive

    def _analyse(
        self,
        jobs: list[Job],
        analyst: Analyst,
        coordinator: Coordinator,
    ) -> list[Job]:
        """Deterministic fit ranking. Opt-in; offline by default."""
        if not jobs:
            analyst.skipped("No jobs to score")
            return jobs
        if not self.settings.fit.enabled:
            analyst.skipped("Fit scoring disabled (JOBSCOUT_FIT_ENABLED=0)")
            return jobs

        self.control.checkpoint("fit scoring")
        try:
            pairs = self._score_jobs(jobs, analyst)
        except RunCancelled:
            raise
        except Exception as exc:
            analyst.failed(f"Fit scoring failed: {str(exc)[:120]}")
            coordinator.log(
                f"Fit scoring skipped after error: {str(exc)[:120]}", level="warn"
            )
            return jobs

        ordered = rank_jobs(pairs)
        try:
            store = ranking_store_for(self.settings)
            for job, fit in ordered:
                store.upsert(job, fit)
        except Exception as exc:
            analyst.log(f"Ranking store not updated: {str(exc)[:120]}", level="warn")

        kept = passing(ordered, self.settings.fit.min_score)
        analyst.ranked(len(kept), self.settings.fit.min_score)
        return kept

    def _score_jobs(
        self,
        jobs: list[Job],
        analyst: Analyst,
    ) -> list[tuple[Job, FitResult]]:
        """Ask the LLM for the four ratings, compute the total ourselves."""
        from jobscout.cover_letter.llm import CoverLetterLLM
        from jobscout.cover_letter.profile import load_profile

        config = self.settings.cover_letter
        client = CoverLetterLLM(
            base_url=config.base_url,
            api_key=config.api_key or "ollama",
            model=config.model,
            temperature=0.0,
            max_tokens=700,
            timeout_seconds=config.timeout_seconds,
            num_ctx=getattr(config, "num_ctx", 4096),
            provider=getattr(config, "provider", "auto"),
            allow_remote=getattr(config, "allow_remote", False),
        )
        profile_text = ""
        candidate_location: str | None = None
        try:
            profile = load_profile(config.profile_path)
            profile_text = profile.prompt_text()
            candidate_location = getattr(profile, "city", None) or None
        except Exception:
            pass

        limit = max(0, int(self.settings.fit.max_jobs))
        pairs: list[tuple[Job, FitResult]] = []
        for index, job in enumerate(jobs[:limit], start=1):
            self.control.checkpoint(f"fit score {index}")
            analyst.ranking(len(jobs[:limit]))
            description = (
                job.description
                or load_saved_description(
                    self.settings, job.source.value, job.job_id
                )
                or job.posted_text
                or ""
            )
            raw = client.score_fit(
                profile_text,
                job,
                description,
                candidate_location=candidate_location,
            )
            scores = parse_fit_payload(raw)
            fit = compute_fit(scores, deadline=job.deadline)
            pairs.append((job, fit))
        return pairs

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

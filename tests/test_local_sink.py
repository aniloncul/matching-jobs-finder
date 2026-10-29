import csv
from pathlib import Path

from jobscout.models import Job
from jobscout.sinks.local import JOB_HEADERS, LocalSink


def make_job(job_id: str = "1", source: str = "linkedin") -> Job:
    return Job(
        company="Acme",
        title="AI Engineer",
        link=f"https://example.com/{job_id}",
        source=source,
        job_id=job_id,
        location="Remote",
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_local_sink_writes_and_dedupes(tmp_path: Path) -> None:
    jobs_csv = tmp_path / "jobs.csv"
    runs_csv = tmp_path / "runs.csv"
    sink = LocalSink(jobs_csv, runs_csv)
    sink.prepare()

    assert jobs_csv.exists()
    assert runs_csv.exists()
    assert jobs_csv.read_text(encoding="utf-8").strip().split(",") == JOB_HEADERS

    keys, fingerprints = sink.existing()
    assert keys == set()
    assert fingerprints == set()

    assert sink.write([make_job()]) == 1
    assert sink.write([make_job()]) == 0
    assert sink.write([make_job("2"), make_job("2", "indeed")]) == 2

    rows = read_rows(jobs_csv)
    assert len(rows) == 3
    assert rows[0]["Company Name"] == "Acme"
    assert rows[0]["Source"] == "linkedin"
    assert rows[2]["Source"] == "indeed"


def test_local_sink_records_run(tmp_path: Path) -> None:
    sink = LocalSink(tmp_path / "jobs.csv", tmp_path / "runs.csv")
    sink.prepare()
    sink.record_run(
        {
            "run_id": "run-1",
            "sources": ["linkedin", "indeed"],
            "mode": "cached",
            "found": 12,
            "new": 12,
            "skipped": 0,
            "errors": 0,
            "status": "completed",
            "started_at": 1.0,
            "finished_at": 2.0,
        }
    )
    rows = read_rows(tmp_path / "runs.csv")
    assert rows[0]["Run ID"] == "run-1"
    assert rows[0]["Sources"] == "linkedin, indeed"
    assert rows[0]["Status"] == "completed"


def test_local_sink_saves_description_and_migrates_old_header(tmp_path: Path) -> None:
    jobs_csv = tmp_path / "jobs.csv"
    runs_csv = tmp_path / "runs.csv"
    old_headers = [header for header in JOB_HEADERS if header != "Description"]
    with jobs_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_headers)
        writer.writeheader()
        writer.writerow({header: "" for header in old_headers} | {
            "Company Name": "Acme",
            "Job Title": "AI Engineer",
            "Job Link": "https://example.com/1",
            "Source": "linkedin",
            "Job ID": "1",
        })

    sink = LocalSink(jobs_csv, runs_csv)
    sink.prepare()
    assert "Description" in read_rows(jobs_csv)[0] or "Description" in jobs_csv.read_text()
    assert sink.save_description("linkedin", "1", "Full job description") is True

    rows = read_rows(jobs_csv)
    assert rows[0]["Description"] == "Full job description"
    assert sink.list_jobs(1)[0].description == "Full job description"


def test_local_sink_delete_job_and_persists_exclusion(tmp_path: Path) -> None:
    from jobscout.config import Settings
    settings = Settings(root=tmp_path)
    jobs_csv = tmp_path / "jobs.csv"
    runs_csv = tmp_path / "runs.csv"
    sink = LocalSink(jobs_csv, runs_csv, settings=settings)
    sink.prepare()

    sink.write([make_job("1", "linkedin"), make_job("2", "indeed")])
    assert len(read_rows(jobs_csv)) == 2

    assert sink.delete_job("linkedin", "1") is True
    rows_after = read_rows(jobs_csv)
    assert len(rows_after) == 1
    assert rows_after[0]["Job ID"] == "2"

    keys, fps = sink.existing()
    assert ("linkedin", "1") in keys

    assert sink.write([make_job("1", "linkedin")]) == 0
    assert len(read_rows(jobs_csv)) == 1

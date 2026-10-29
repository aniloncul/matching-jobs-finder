from datetime import datetime, timezone

from jobscout.parsers.dates import parse_date_value, parse_relative_date
from jobscout.parsers.indeed import parse_indeed_html
from jobscout.parsers.linkedin import parse_linkedin_html
from jobscout.parsers.urls import (
    extract_indeed_job_id,
    extract_linkedin_job_id,
    indeed_link,
    linkedin_link,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def test_parse_iso_date() -> None:
    assert parse_date_value("2026-10-20T10:15:00Z") == "2026-10-20"
    assert parse_date_value("2026-10-20") == "2026-10-20"
    assert parse_date_value("not a date") is None
    assert parse_date_value(None) is None


def test_parse_relative_dates() -> None:
    assert parse_relative_date("3 days ago", NOW) == ("2026-10-18", True)
    assert parse_relative_date("just posted", NOW) == ("2026-10-21", True)
    assert parse_relative_date("yesterday", NOW) == ("2026-10-20", True)
    assert parse_relative_date("2 weeks ago", NOW) == ("2026-10-07", True)
    assert parse_relative_date("Vor 3 Tagen", NOW) == ("2026-10-18", True)
    assert parse_relative_date("Vor 2 Stunden veröffentlicht", NOW) == (
        "2026-10-21",
        True,
    )
    assert parse_relative_date("Gestern", NOW) == ("2026-10-20", True)
    assert parse_relative_date("no date here", NOW) == (None, False)


def test_extract_linkedin_ids_and_links() -> None:
    href = "https://www.linkedin.com/jobs/view/1234567890/?refId=abc"
    assert extract_linkedin_job_id(href) == "1234567890"
    assert extract_linkedin_job_id("/jobs/view/ai-engineer-at-acme-1234567890") == (
        "1234567890"
    )
    assert linkedin_link(href, "1234567890") == (
        "https://www.linkedin.com/jobs/view/1234567890"
    )


def test_extract_indeed_ids_and_links() -> None:
    href = "https://www.indeed.com/viewjob?jk=abc123&from=serp"
    assert extract_indeed_job_id(href) == "abc123"
    assert extract_indeed_job_id("/viewjob?jk=xyz789") == "xyz789"
    assert indeed_link(href, "abc123", "https://www.indeed.com") == (
        "https://www.indeed.com/viewjob?jk=abc123"
    )


def test_parse_linkedin_current_card_markup() -> None:
    html = """
    <li data-occludable-job-id="4478679498">
      <a class="job-card-container__link" aria-label="AI Engineer with verification"
         href="/jobs/view/4478679498/?refId=abc">
        <span aria-hidden="true"><strong>AI Engineer</strong></span>
        <span class="visually-hidden">AI Engineer</span>
      </a>
      <div class="artdeco-entity-lockup__subtitle">Acme</div>
      <ul class="job-card-container__metadata-wrapper">
        <li><span>Berlin</span></li>
      </ul>
      <time datetime="2026-10-20">Vor 14 Stunden
        <span class="visually-hidden">Innerhalb der letzten 24 Stunden</span>
      </time>
    </li>
    """
    jobs = parse_linkedin_html(html, "AI Engineer")
    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
    assert jobs[0].title == "AI Engineer"
    assert jobs[0].location == "Berlin"
    assert jobs[0].posted_text == "Vor 14 Stunden"
    assert jobs[0].posted_date == "2026-10-20"
    assert jobs[0].job_id == "4478679498"
    assert jobs[0].link == "https://www.linkedin.com/jobs/view/4478679498"


def test_parse_linkedin_new_search_results_markup() -> None:
    html = """
    <div role="button" componentkey="job-card-component-ref-4482055502">
      <p>
        <span>Ausgewählt, </span>
        <span>Agentic AI Software Engineer</span>
        <span aria-hidden="true">Agentic AI Software Engineer</span>
      </p>
      <p>Machine Learning Reply GmbH</p>
      <p>Berlin (Hybrid)</p>
      <p>Vor 14 Stunden veröffentlicht<span>Vor 14 Stunden</span></p>
    </div>
    """
    jobs = parse_linkedin_html(html, "n8n")
    assert len(jobs) == 1
    assert jobs[0].company == "Machine Learning Reply GmbH"
    assert jobs[0].title == "Agentic AI Software Engineer"
    assert jobs[0].location == "Berlin (Hybrid)"
    assert jobs[0].posted_text == "Vor 14 Stunden veröffentlicht"
    assert jobs[0].posted_date is not None
    assert jobs[0].estimated is True
    assert jobs[0].job_id == "4482055502"
    assert jobs[0].link == "https://www.linkedin.com/jobs/view/4482055502"


def test_parse_indeed_current_card_markup() -> None:
    html = """
    <div class="job_seen_beacon">
      <h3 class="jobTitle">
        <a class="jcs-JobTitle" data-jk="abc123" href="/rc/clk?jk=abc123">
          <span title="AI Engineer">AI Engineer</span>
        </a>
      </h3>
      <div data-testid="company-name">Acme</div>
      <div data-testid="text-location">Berlin</div>
    </div>
    """
    jobs = parse_indeed_html(html, "AI Engineer", base_url="https://de.indeed.com")
    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
    assert jobs[0].title == "AI Engineer"
    assert jobs[0].location == "Berlin"
    assert jobs[0].job_id == "abc123"
    assert jobs[0].link == "https://de.indeed.com/viewjob?jk=abc123"


def test_parse_indeed_embedded_job_metadata() -> None:
    html = """
    <div class="job_seen_beacon">
      <h3 class="jobTitle">
        <a class="jcs-JobTitle" data-jk="abc123" href="/rc/clk?jk=abc123">
          <span title="AI Engineer">AI Engineer</span>
        </a>
      </h3>
      <div data-testid="company-name">Acme</div>
      <div data-testid="text-location">Berlin</div>
    </div>
    <script>
      window.mosaic.providerData["mosaic-provider-jobcards"] = {
        "metaData": {
          "mosaicProviderJobCardsModel": {
            "results": [
              {
                "jobkey": "abc123",
                "createDate": 1792060335405,
                "formattedRelativeTime": "vor 6 Tagen"
              }
            ]
          }
        }
      };
    </script>
    """
    jobs = parse_indeed_html(html, "AI Engineer", base_url="https://de.indeed.com")
    assert len(jobs) == 1
    assert jobs[0].posted_text == "vor 6 Tagen"
    assert jobs[0].posted_date == "2026-10-15"
    assert jobs[0].estimated is False

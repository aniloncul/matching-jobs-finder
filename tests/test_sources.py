from urllib.parse import parse_qs, urlparse

from jobscout.config import LinkedInConfig
from jobscout.sources.linkedin import _search_url


def test_linkedin_mid_experience_uses_mid_senior_filter() -> None:
    url = _search_url(
        LinkedInConfig(query="n8n", location="Berlin", experience="mid")
    )
    params = parse_qs(urlparse(url).query)
    assert params["f_E"] == ["4"]
    assert params["keywords"] == ["n8n"]
    assert params["location"] == ["Berlin"]

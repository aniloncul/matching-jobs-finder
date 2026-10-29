from jobscout.queries import per_query_limit, split_queries


def test_split_queries_handles_boolean_or() -> None:
    assert split_queries('n8n OR "low-code" OR workflow automation') == [
        "n8n",
        '"low-code"',
        "workflow automation",
    ]


def test_split_queries_handles_commas_and_newlines() -> None:
    assert split_queries("n8n, low-code; Zapier\nMake.com") == [
        "n8n",
        "low-code",
        "Zapier",
        "Make.com",
    ]


def test_split_queries_preserves_quoted_or_and_dedupes() -> None:
    assert split_queries('"R&D OR lab" OR n8n OR N8N') == [
        '"R&D OR lab"',
        "n8n",
    ]


def test_split_queries_empty() -> None:
    assert split_queries("   ") == []


def test_per_query_limit_distributes_total() -> None:
    assert per_query_limit(25, 6) == 4
    assert per_query_limit(25, 8) == 3
    assert per_query_limit(25, 0) == 25

from __future__ import annotations

MAX_QUERIES = 8


def split_queries(raw: str) -> list[str]:
    if not raw:
        return []

    parts: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(raw):
        char = raw[i]
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {'"', "'"}:
            quote = char
            buffer.append(char)
            i += 1
            continue
        if char in {",", ";", "\n"}:
            parts.append("".join(buffer))
            buffer = []
            i += 1
            continue
        if _is_or(raw, i):
            parts.append("".join(buffer))
            buffer = []
            i += 4 if raw[i : i + 4].upper() == "VEYA" else 2
            while i < len(raw) and raw[i].isspace():
                i += 1
            continue
        buffer.append(char)
        i += 1
    parts.append("".join(buffer))

    queries: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = " ".join(part.split()).strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        queries.append(value)
    return queries[:MAX_QUERIES]


def _is_or(raw: str, index: int) -> bool:
    for token in ("OR", "VEYA"):
        end = index + len(token)
        if raw[index:end].upper() != token:
            continue
        before = raw[index - 1] if index > 0 else " "
        after = raw[end] if end < len(raw) else " "
        if before.isspace() and after.isspace():
            return True
    return False


def per_query_limit(total: int, query_count: int) -> int:
    if query_count <= 0:
        return max(1, total)
    return max(1, total // query_count)

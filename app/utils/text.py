import re
import unicodedata


_WHITESPACE = re.compile(r"\s+")


def normalize_query(value: str) -> str:
    """Normalize user input without transliterating or changing its meaning."""
    normalized = unicodedata.normalize("NFKC", value)
    return _WHITESPACE.sub(" ", normalized).strip()


def comparison_key(value: str) -> str:
    return normalize_query(value).casefold()

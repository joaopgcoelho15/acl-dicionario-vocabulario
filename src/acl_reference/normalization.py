from __future__ import annotations

import re
import unicodedata


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def search_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", clean_text(value).casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


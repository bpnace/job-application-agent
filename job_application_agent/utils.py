from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

from pydantic import BaseModel


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slugify(value: str, max_length: int = 72) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return (slug[:max_length].strip("-") or "item")


def listing_key(source_url: str, title: str, company: str) -> str:
    digest = hashlib.sha1(f"{source_url}|{title}|{company}".encode("utf-8")).hexdigest()[:10]
    return f"{slugify(company, 28)}-{slugify(title, 34)}-{digest}"


def absolute_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href or "")


def write_json(path: Path, data: BaseModel | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_text_limited(path: Path, limit: int = 5000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")[:limit]

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    id: str                  # content_hash used as stable ID
    title: str
    url: str
    body: str                # cleaned plain text (HTML stripped + entities decoded)
    category: str
    source_id: str
    priority: int            # from sources.yaml (1 = highest)
    published_at: datetime
    content_hash: str = ""
    score: float = 0.0
    matched_keywords: list = field(default_factory=list)  # populated by filter
    summary: str = ""        # placeholder; filled by Claude in Day 2


@dataclass
class Brief:
    generated_at: datetime
    items: list[Item]
    date_range_start: datetime
    date_range_end: datetime

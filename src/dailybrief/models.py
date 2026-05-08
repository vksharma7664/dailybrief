from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    id: str
    title: str
    url: str
    body: str
    category: str
    source_id: str
    priority: int
    published_at: datetime
    content_hash: str = ""
    score: float = 0.0
    matched_keywords: list = field(default_factory=list)
    summary: str = ""            # filled by Claude summarizer (Day 2)
    relevance_score: int = 0     # 1–10, from Claude (Day 2)
    why_it_matters: str = ""     # top-3 only, from Claude (Day 2)


@dataclass
class Summary:
    summary: str
    category: str
    relevance_score: int


@dataclass
class Brief:
    generated_at: datetime
    items: list[Item]            # all items (top3 + remaining)
    date_range_start: datetime
    date_range_end: datetime
    top3: list[Item] = field(default_factory=list)

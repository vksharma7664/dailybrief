"""Fetcher protocol — all source adapters must satisfy this interface."""
from typing import Protocol, runtime_checkable

from ..models import Item


@runtime_checkable
class Fetcher(Protocol):
    async def fetch(
        self,
        source_id: str,
        url: str,
        category: str = "Other",
        priority: int = 2,
    ) -> list[Item]:
        """Fetch items from *url*, tagged with *source_id* and *category*.

        Returns an empty list (never raises) if the source is unavailable —
        callers are responsible for logging errors.
        """
        ...

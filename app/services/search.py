from __future__ import annotations


class SearchService:
    async def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        return []


search_service = SearchService()

from __future__ import annotations

import os
import time
from typing import Any

import httpx


SUGGEST_PARTY_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"


class DadataService:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.token = os.getenv("DADATA_API_KEY", "").strip()
        self.cache_ttl = int(os.getenv("DADATA_CACHE_TTL_SECONDS", "86400"))
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def configuration(self) -> dict[str, Any]:
        return {"status": "configured" if self.token else "optional", "provider": "DaData", "api_key_required": True}

    @staticmethod
    def normalize(item: dict[str, Any]) -> dict[str, Any]:
        data = item.get("data") or {}
        name = data.get("name") or {}
        address = data.get("address") or {}
        management = data.get("management") or {}
        state = data.get("state") or {}
        return {
            "value": item.get("value"),
            "inn": data.get("inn"),
            "kpp": data.get("kpp"),
            "ogrn": data.get("ogrn"),
            "name_full": name.get("full_with_opf"),
            "name_short": name.get("short_with_opf"),
            "legal_address": address.get("value"),
            "management_name": management.get("name"),
            "status": state.get("status"),
        }

    async def suggest(self, query: str) -> dict[str, Any]:
        normalized_query = " ".join(query.strip().split())
        key = normalized_query.casefold()
        cached = self._cache.get(key)
        now = time.time()
        if cached and now - cached[0] < self.cache_ttl:
            return {"suggestions": cached[1], "source": "cache"}
        if not self.token:
            return {"suggestions": [], "source": "manual", "status": "not_configured"}
        response = await self.http.post(
            SUGGEST_PARTY_URL,
            headers={"Authorization": f"Token {self.token}", "Accept": "application/json"},
            json={"query": normalized_query, "count": 7},
            timeout=10,
        )
        response.raise_for_status()
        suggestions = [self.normalize(item) for item in response.json().get("suggestions") or []]
        self._cache[key] = (now, suggestions)
        if len(self._cache) > 500:
            oldest = min(self._cache, key=lambda item: self._cache[item][0])
            self._cache.pop(oldest, None)
        return {"suggestions": suggestions, "source": "DaData", "status": "ok"}

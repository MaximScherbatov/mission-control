from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class AircraftTrafficService:
    """Optional ADS-B traffic adapter with provider failover and quota-aware cache."""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.source = os.getenv("AIRCRAFT_SOURCE", "auto").strip().lower()
        self.readsb_url = os.getenv("READSB_URL", "").strip()
        self.airplanes_enabled = os.getenv("AIRPLANES_LIVE_ENABLED", "false").lower() == "true"
        self.opensky_client_id = os.getenv("OPENSKY_CLIENT_ID", "").strip()
        self.opensky_client_secret = os.getenv("OPENSKY_CLIENT_SECRET", "").strip()
        self.center = (
            _env_float("AIRCRAFT_CENTER_LAT", 55.7558),
            _env_float("AIRCRAFT_CENTER_LON", 37.6173),
        )
        self.radius_nm = max(1, min(250, int(_env_float("AIRCRAFT_RADIUS_NM", 130))))
        self.bbox = (
            _env_float("AIRCRAFT_BBOX_MIN_LAT", 54.2),
            _env_float("AIRCRAFT_BBOX_MIN_LON", 34.8),
            _env_float("AIRCRAFT_BBOX_MAX_LAT", 57.3),
            _env_float("AIRCRAFT_BBOX_MAX_LON", 41.2),
        )
        # Anonymous OpenSky has a small daily quota; 240 s stays below 400 calls/day.
        # A standard authenticated account has 4,000 state credits/day. A
        # 25-second interval stays below that budget for our <=25 sq° bbox.
        default_ttl = 25 if self.opensky_client_id and self.opensky_client_secret else 240
        if self.source == "readsb":
            default_ttl = 3
        minimum_ttl = 2 if self.source == "readsb" else 10
        self.cache_ttl = max(minimum_ttl, int(_env_float("AIRCRAFT_CACHE_TTL_SECONDS", default_ttl)))
        self.stale_ttl = max(self.cache_ttl, int(_env_float("AIRCRAFT_STALE_TTL_SECONDS", 1800)))
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = asyncio.Lock()

    def configuration(self) -> dict[str, Any]:
        return {
            "status": "optional",
            "provider": self.source,
            "readsb_configured": bool(self.readsb_url),
            "opensky_authenticated": bool(self.opensky_client_id and self.opensky_client_secret),
            "airplanes_live_enabled": self.airplanes_enabled,
            "affects_missions": False,
        }

    def _provider_order(self) -> list[str]:
        if self.source != "auto":
            return [self.source]
        order: list[str] = []
        if self.readsb_url:
            order.append("readsb")
        order.append("opensky")
        # airplanes.live now requires project approval and returns 403 otherwise.
        if self.airplanes_enabled:
            order.append("airplanes")
        return order

    @staticmethod
    def _readsb_items(items: list[dict[str, Any]] | None, source: str) -> list[dict[str, Any]]:
        result = []
        now = time.time()
        for item in items or []:
            lat, lon = item.get("lat"), item.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            alt_baro = item.get("alt_baro")
            on_ground = alt_baro == "ground"
            altitude = None if on_ground or not isinstance(alt_baro, (int, float)) else round(alt_baro * 0.3048)
            speed = item.get("gs")
            position_age = max(0.0, float(item.get("seen_pos") or 0))
            contact_age = max(0.0, float(item.get("seen") or 0))
            result.append({
                "id": item.get("hex") or f"{lat:.5f}:{lon:.5f}",
                "callsign": (item.get("flight") or "").strip() or None,
                "lat": lat,
                "lon": lon,
                "altitude_m": altitude,
                "speed_kmh": round(speed * 1.852) if isinstance(speed, (int, float)) else None,
                "heading_deg": item.get("track") if isinstance(item.get("track"), (int, float)) else 0,
                "vertical_speed_mps": round(item["baro_rate"] * 0.00508, 1) if isinstance(item.get("baro_rate"), (int, float)) else None,
                "aircraft_type": item.get("t") or None,
                "on_ground": on_ground,
                "position_time": now - position_age,
                "last_contact_time": now - contact_age,
                "position_age_seconds": round(position_age, 1),
                "last_contact_age_seconds": round(contact_age, 1),
                "position_source": "ADS-B",
                "source": source,
            })
        return result

    async def _from_readsb(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not self.readsb_url:
            raise RuntimeError("READSB_URL не настроен")
        response = await self.http.get(self.readsb_url, timeout=8)
        response.raise_for_status()
        return self._readsb_items(response.json().get("aircraft"), "readsb"), {}

    async def _from_airplanes(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        lat, lon = self.center
        response = await self.http.get(
            f"https://api.airplanes.live/v2/point/{lat}/{lon}/{self.radius_nm}", timeout=12
        )
        if response.status_code == 403:
            raise RuntimeError("доступ требует согласования проекта с airplanes.live")
        response.raise_for_status()
        return self._readsb_items(response.json().get("ac"), "airplanes.live"), {}

    async def _opensky_token(self, force: bool = False) -> str | None:
        if not self.opensky_client_id or not self.opensky_client_secret:
            return None
        now = time.time()
        if not force and self._token and now < self._token_expires_at:
            return self._token
        response = await self.http.post(
            "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.opensky_client_id,
                "client_secret": self.opensky_client_secret,
            },
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = now + max(30, float(payload.get("expires_in", 1800)) - 60)
        return self._token

    async def _from_opensky(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        min_lat, min_lon, max_lat, max_lon = self.bbox
        token = await self._opensky_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await self.http.get(
            "https://opensky-network.org/api/states/all",
            params={"lamin": min_lat, "lomin": min_lon, "lamax": max_lat, "lomax": max_lon},
            headers=headers,
            timeout=15,
        )
        if response.status_code == 401 and token:
            token = await self._opensky_token(force=True)
            response = await self.http.get(
                "https://opensky-network.org/api/states/all",
                params={"lamin": min_lat, "lomin": min_lon, "lamax": max_lat, "lomax": max_lon},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
        if response.status_code == 429:
            retry = response.headers.get("X-Rate-Limit-Retry-After-Seconds", "неизвестно")
            raise RuntimeError(f"исчерпана квота OpenSky, повтор через {retry} с")
        response.raise_for_status()
        result = []
        position_names = {0: "ADS-B", 1: "ASTERIX", 2: "MLAT", 3: "FLARM"}
        body = response.json()
        snapshot_time = float(body.get("time") or time.time())
        for state in body.get("states") or []:
            if len(state) < 17 or state[5] is None or state[6] is None:
                continue
            position_time = float(state[3]) if state[3] is not None else snapshot_time
            last_contact_time = float(state[4]) if state[4] is not None else snapshot_time
            position_age = max(0.0, snapshot_time - position_time)
            if position_age > 30:
                continue
            result.append({
                "id": state[0],
                "callsign": (state[1] or "").strip() or None,
                "lat": state[6],
                "lon": state[5],
                "altitude_m": round(state[7]) if state[7] is not None else None,
                "speed_kmh": round(state[9] * 3.6) if state[9] is not None else None,
                "heading_deg": state[10] if state[10] is not None else 0,
                "vertical_speed_mps": round(state[11], 1) if state[11] is not None else None,
                "aircraft_type": None,
                "on_ground": bool(state[8]),
                "position_time": position_time,
                "last_contact_time": last_contact_time,
                "position_age_seconds": round(position_age, 1),
                "last_contact_age_seconds": round(max(0.0, snapshot_time - last_contact_time), 1),
                "position_source": position_names.get(state[16], "другой"),
                "source": "OpenSky",
            })
        meta = {
            "rate_limit_remaining": response.headers.get("X-Rate-Limit-Remaining"),
            "authenticated": bool(token),
        }
        return result, meta

    async def _provider(self, name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if name == "readsb":
            return await self._from_readsb()
        if name == "opensky":
            return await self._from_opensky()
        if name in {"airplanes", "airplanes.live"}:
            return await self._from_airplanes()
        raise RuntimeError(f"неизвестный источник {name}")

    async def get(self) -> dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache_at < self.cache_ttl:
            return {**self._cache, "cache": "fresh"}
        async with self._lock:
            now = time.time()
            if self._cache and now - self._cache_at < self.cache_ttl:
                return {**self._cache, "cache": "fresh"}
            diagnostics = []
            for provider in self._provider_order():
                try:
                    aircraft, meta = await self._provider(provider)
                    diagnostics.append({"provider": provider, "status": "ok", **meta})
                    payload = {
                        "status": "live",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "source": provider,
                        "count": len(aircraft),
                        "airborne_count": sum(not item["on_ground"] for item in aircraft),
                        "ground_count": sum(item["on_ground"] for item in aircraft),
                        "refresh_after_seconds": self.cache_ttl,
                        "aircraft": aircraft,
                        "providers": diagnostics,
                        "notice": "Информационный слой; не заменяет официальные данные об использовании воздушного пространства.",
                    }
                    self._cache = payload
                    self._cache_at = now
                    return {**payload, "cache": "miss"}
                except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as error:
                    diagnostics.append({"provider": provider, "status": "error", "message": str(error)[:180]})
            if self._cache and now - self._cache_at <= self.stale_ttl:
                return {
                    **self._cache,
                    "status": "stale",
                    "cache": "stale",
                    "age_seconds": round(now - self._cache_at),
                    "providers": diagnostics,
                }
            return {
                "status": "unavailable",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "source": None,
                "count": 0,
                "airborne_count": 0,
                "ground_count": 0,
                "refresh_after_seconds": self.cache_ttl,
                "aircraft": [],
                "providers": diagnostics,
                "notice": "Источники воздушного движения временно недоступны; работа миссий не затронута.",
            }

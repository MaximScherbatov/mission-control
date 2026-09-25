from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import numpy as np
from PIL import Image


log = logging.getLogger("mission-control.weather")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
METEORAD_URL = "https://meteoinfo.ru/hmc-output/rmap/phenomena.gif"

RAD_LAT_M = 54.39384615384615
RAD_LON_M = 39.20128205128205
RAD_CFX = [560.56109, 186.055398, 13.84616, -1.152778, 377.672373, -58.970165, -1.624755, 6.602257, 0.066164, -1.075946]
RAD_CFY = [533.653597, 220.928787, -17.802402, -0.512532, -318.191635, -46.057263, 5.156415, 15.393124, -7.895715, -3.327381]
RAD_LON0, RAD_LON1 = 19.0, 62.0
RAD_LAT0, RAD_LAT1 = 41.5, 66.0
RAD_SRC = 1200


class WeatherService:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.forecast_ttl = int(os.getenv("WEATHER_CACHE_TTL_SECONDS", "600"))
        self.radar_ttl = int(os.getenv("RADAR_CACHE_TTL_SECONDS", "300"))
        self.radar_out = max(600, min(1600, int(os.getenv("RADAR_OUTPUT_PX", "900"))))
        self.radar_frames_limit = max(4, min(19, int(os.getenv("RADAR_FRAMES", "12"))))
        self._forecast_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._radar_at = 0.0
        self._radar_version = 0
        self._radar_frames: list[bytes] = []
        self._radar_lock = asyncio.Lock()
        self._radar_update: asyncio.Task | None = None
        self._grids: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None

    def configuration(self) -> dict[str, Any]:
        return {"status": "optional", "provider": "Open-Meteo / МЕТЕОРАД", "api_key_required": False}

    async def forecast(self, lat: float, lon: float) -> dict[str, Any]:
        key = f"{lat:.2f}:{lon:.2f}"
        now = time.time()
        cached = self._forecast_cache.get(key)
        if cached and now - cached[0] < self.forecast_ttl:
            return {**cached[1], "cache": "fresh"}
        try:
            response = await self.http.get(
                OPEN_METEO_URL,
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,surface_pressure,precipitation,rain,snowfall,cloud_cover,wind_speed_10m,wind_direction_10m,visibility,weather_code,is_day",
                    "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility,cloud_cover",
                    "forecast_days": 3, "wind_speed_unit": "ms", "timezone": "Europe/Moscow",
                },
                timeout=12,
            )
            response.raise_for_status()
            raw = response.json()
            current = raw.get("current") or {}
            hourly = raw.get("hourly") or {}
            hours = []
            for index, timestamp in enumerate(hourly.get("time") or []):
                hours.append({
                    "time": timestamp,
                    "temperature_c": _at(hourly, "temperature_2m", index),
                    "precipitation_probability": _at(hourly, "precipitation_probability", index),
                    "precipitation_mm": _at(hourly, "precipitation", index),
                    "weather_code": _at(hourly, "weather_code", index),
                    "wind_speed_mps": _at(hourly, "wind_speed_10m", index),
                    "wind_direction_deg": _at(hourly, "wind_direction_10m", index),
                    "wind_gust_mps": _at(hourly, "wind_gusts_10m", index),
                    "visibility_m": _at(hourly, "visibility", index),
                    "cloud_cover_percent": _at(hourly, "cloud_cover", index),
                })
            payload = {
                "status": "live", "source": "Open-Meteo", "observed_at": current.get("time"),
                "current": {
                    "temperature_c": current.get("temperature_2m"), "precipitation_mm": current.get("precipitation"),
                    "rain_mm": current.get("rain"), "snowfall_cm": current.get("snowfall"),
                    "relative_humidity_percent": current.get("relative_humidity_2m"),
                    "surface_pressure_hpa": current.get("surface_pressure"),
                    "cloud_cover_percent": current.get("cloud_cover"), "wind_speed_mps": current.get("wind_speed_10m"),
                    "wind_direction_deg": current.get("wind_direction_10m"), "visibility_m": current.get("visibility"),
                    "weather_code": current.get("weather_code"), "is_day": current.get("is_day"),
                },
                "hourly": hours,
            }
            payload["risk"] = weather_risk(payload["current"])
            self._forecast_cache[key] = (now, payload)
            return {**payload, "cache": "miss"}
        except (httpx.HTTPError, ValueError, KeyError) as error:
            if cached:
                return {**cached[1], "status": "stale", "cache": "stale", "age_seconds": round(now - cached[0])}
            raise RuntimeError("Прогноз погоды недоступен") from error

    def _poly_terms(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        return np.stack([(lat ** i) * (lon ** j) for i in range(4) for j in range(4 - i)], axis=-1)

    def _radar_grids(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self._grids is not None:
            return self._grids
        radius = 6378137.0
        mx0, mx1 = radius * math.radians(RAD_LON0), radius * math.radians(RAD_LON1)
        my0 = radius * math.log(math.tan(math.pi / 4 + math.radians(RAD_LAT0) / 2))
        my1 = radius * math.log(math.tan(math.pi / 4 + math.radians(RAD_LAT1) / 2))
        col = np.arange(self.radar_out, dtype=np.float64)
        row = np.arange(self.radar_out, dtype=np.float64)
        gx = mx0 + (col + .5) / self.radar_out * (mx1 - mx0)
        gy = my1 - (row + .5) / self.radar_out * (my1 - my0)
        grid_x, grid_y = np.meshgrid(gx, gy)
        lon = np.degrees(grid_x / radius)
        lat = np.degrees(2 * np.arctan(np.exp(grid_y / radius)) - math.pi / 2)
        matrix = self._poly_terms((lat - RAD_LAT_M) / 10, (lon - RAD_LON_M) / 10)
        source_x = (matrix @ np.array(RAD_CFX)).astype(np.float32)
        source_y = (matrix @ np.array(RAD_CFY)).astype(np.float32)
        valid = (source_x >= 0) & (source_x < RAD_SRC - 1) & (source_y >= 0) & (source_y < RAD_SRC - 1)
        self._grids = (source_x, source_y, valid)
        return self._grids

    @staticmethod
    def _extract_echo(frame: Image.Image) -> np.ndarray:
        rgb = np.asarray(frame.convert("RGB"), dtype=np.float32) / 255
        maximum, minimum = rgb.max(axis=-1), rgb.min(axis=-1)
        saturation = np.where(maximum > 0, (maximum - minimum) / np.maximum(maximum, 1e-6), 0)
        echo = (saturation > .35) & (maximum > .35)
        echo[0:460, 0:148] = False
        echo[0:40, 160:370] = False
        echo[0:40, 840:RAD_SRC] = False
        echo[1150:RAD_SRC, :] = False
        echo[1055:RAD_SRC, 0:172] = False
        output = np.zeros((*echo.shape, 4), dtype=np.uint8)
        output[..., :3] = (rgb * 255).astype(np.uint8)
        output[..., 3] = echo.astype(np.uint8) * 255
        return output

    @staticmethod
    def _warp(source: np.ndarray, sx: np.ndarray, sy: np.ndarray, valid: np.ndarray) -> np.ndarray:
        x0 = np.clip(sx.astype(np.int32), 0, RAD_SRC - 2)
        y0 = np.clip(sy.astype(np.int32), 0, RAD_SRC - 2)
        dx, dy = np.clip(sx - x0, 0, 1)[..., None], np.clip(sy - y0, 0, 1)[..., None]
        source = source.astype(np.float32)
        output = (source[y0, x0] * (1-dx) * (1-dy) + source[y0, x0+1] * dx * (1-dy)
                  + source[y0+1, x0] * (1-dx) * dy + source[y0+1, x0+1] * dx * dy).astype(np.uint8)
        output[~valid] = 0
        output[output[..., 3] < 90] = 0
        return output

    def _build_radar(self, content: bytes) -> list[bytes]:
        sx, sy, valid = self._radar_grids()
        image = Image.open(io.BytesIO(content))
        first = max(0, getattr(image, "n_frames", 1) - self.radar_frames_limit)
        frames = []
        for index in range(first, getattr(image, "n_frames", 1)):
            image.seek(index)
            warped = self._warp(self._extract_echo(image), sx, sy, valid)
            output = io.BytesIO()
            Image.fromarray(warped).save(output, "PNG", compress_level=5)
            frames.append(output.getvalue())
        return frames

    async def _load_radar(self) -> None:
        async with self._radar_lock:
            if self._radar_frames and time.time() - self._radar_at < self.radar_ttl:
                return
            response = await self.http.get(METEORAD_URL, timeout=25)
            response.raise_for_status()
            frames = await asyncio.to_thread(self._build_radar, response.content)
            if not frames:
                raise RuntimeError("МЕТЕОРАД не вернул кадров")
            self._radar_frames = frames
            self._radar_at = time.time()
            self._radar_version += 1

    async def radar_manifest(self) -> dict[str, Any]:
        now = time.time()
        if self._radar_frames and now - self._radar_at >= self.radar_ttl:
            if self._radar_update is None or self._radar_update.done():
                self._radar_update = asyncio.create_task(self._load_radar())
        elif not self._radar_frames:
            await self._load_radar()
        last = int((self._radar_at or now) // 600) * 600 - 600
        count = len(self._radar_frames)
        return {
            "status": "live" if now - self._radar_at < self.radar_ttl * 2 else "stale",
            "source": "МЕТЕОРАД · ЦАО Росгидромета", "updated": self._radar_at,
            "bbox": [[RAD_LON0, RAD_LAT0], [RAD_LON1, RAD_LAT1]],
            "frames": [{"index": index, "time": last - (count - 1 - index) * 600,
                        "url": f"/api/weather/radar/frame/{index}.png?v={self._radar_version}"}
                       for index in range(count)],
        }

    async def radar_frame(self, index: int) -> bytes | None:
        if not self._radar_frames:
            await self._load_radar()
        if index < 0 or index >= len(self._radar_frames):
            return None
        return self._radar_frames[index]


def _at(values: dict[str, Any], key: str, index: int) -> Any:
    sequence = values.get(key) or []
    return sequence[index] if index < len(sequence) else None


def weather_risk(current: dict[str, Any]) -> dict[str, Any]:
    wind = float(current.get("wind_speed_mps") or 0)
    precipitation = float(current.get("precipitation_mm") or 0)
    visibility = float(current.get("visibility_m") or 20000)
    clouds = float(current.get("cloud_cover_percent") or 0)
    penalties = min(50, wind * 3) + min(30, precipitation * 15) + (25 if visibility < 5000 else 10 if visibility < 10000 else 0) + (8 if clouds > 90 else 0)
    score = max(0, round(100 - penalties))
    level = "critical" if score < 45 else "attention" if score < 75 else "good"
    reasons = []
    if wind >= 10: reasons.append("ветер близок к пределам части флота")
    if precipitation > 0: reasons.append("зафиксированы осадки")
    if visibility < 10000: reasons.append("ограниченная видимость")
    if not reasons: reasons.append("существенных погодных ограничений не выявлено")
    return {"score": score, "level": level, "reasons": reasons}

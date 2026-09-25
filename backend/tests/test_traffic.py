import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.traffic import AircraftTrafficService


class TrafficServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_opensky_is_normalized_and_cached(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            state = ["abc123", "TEST42 ", "Test", 1, 1, 37.6, 55.8, 1200.4, False, 50.0, 271.2, -1.5, None, None, None, False, 0]
            return httpx.Response(200, json={"time": 1, "states": [state]}, headers={"X-Rate-Limit-Remaining": "399"})

        with patch.dict(os.environ, {"AIRCRAFT_SOURCE": "opensky", "AIRCRAFT_CACHE_TTL_SECONDS": "240"}, clear=False):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                service = AircraftTrafficService(client)
                first = await service.get()
                second = await service.get()

        self.assertEqual(calls, 1)
        self.assertEqual(first["status"], "live")
        self.assertEqual(second["cache"], "fresh")
        self.assertEqual(first["aircraft"][0]["callsign"], "TEST42")
        self.assertEqual(first["aircraft"][0]["speed_kmh"], 180)
        self.assertEqual(first["aircraft"][0]["position_source"], "ADS-B")
        self.assertEqual(first["aircraft"][0]["position_time"], 1)
        self.assertEqual(first["airborne_count"], 1)
        self.assertEqual(first["ground_count"], 0)
        self.assertEqual(first["refresh_after_seconds"], 240)

    async def test_failed_sources_return_structured_unavailable_state(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text=json.dumps({"detail": "rate limited"}), headers={"X-Rate-Limit-Retry-After-Seconds": "600"})

        with patch.dict(os.environ, {"AIRCRAFT_SOURCE": "opensky", "OPENSKY_CLIENT_ID": "", "OPENSKY_CLIENT_SECRET": ""}, clear=False):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await AircraftTrafficService(client).get()

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["aircraft"], [])
        self.assertIn("квота", result["providers"][0]["message"])

    def test_readsb_normalization_drops_records_without_position(self):
        values = AircraftTrafficService._readsb_items([
            {"hex": "a", "flight": " ABC ", "lat": 55.7, "lon": 37.6, "alt_baro": 1000, "gs": 100, "baro_rate": 200},
            {"hex": "b", "flight": "NO-POSITION"},
        ], "readsb")
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["altitude_m"], 305)
        self.assertEqual(values[0]["speed_kmh"], 185)
        self.assertEqual(values[0]["vertical_speed_mps"], 1.0)
        self.assertIn("position_time", values[0])
        self.assertIn("position_age_seconds", values[0])


if __name__ == "__main__":
    unittest.main()

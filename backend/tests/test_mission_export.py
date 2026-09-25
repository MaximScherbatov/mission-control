import json
import unittest

from app.mission_export import build_flight_plan, export_mission


RESULT = {
    "mission_id": "demo",
    "mission": {"name": "Тестовая миссия"},
    "recommended_plan_id": "economy",
    "plans": [{
        "id": "economy",
        "vehicles": [{
            "uav_id": "uav-1", "uav_name": "Геоскан 201", "altitude_m": 120, "speed_kmh": 72,
            "route": {"type": "LineString", "coordinates": [[37.0, 55.0], [37.1, 55.1]]},
        }],
    }],
}


class MissionExportTests(unittest.TestCase):
    def test_geojson_export_keeps_route_properties(self):
        content, media_type, filename = export_mission(RESULT, "geojson")
        payload = json.loads(content)
        self.assertEqual(media_type, "application/geo+json")
        self.assertTrue(filename.endswith(".geojson"))
        self.assertEqual(payload["features"][0]["properties"]["altitude_m"], 120)

    def test_kml_export_uses_relative_altitude(self):
        content, media_type, filename = export_mission(RESULT, "kml")
        text = content.decode("utf-8")
        self.assertIn("clampToGround", text)
        self.assertIn("37.00000000,55.00000000", text)
        self.assertNotIn("37.00000000,55.00000000,120.0", text)
        self.assertEqual(media_type, "application/vnd.google-earth.kml+xml")
        self.assertTrue(filename.endswith(".kml"))

    def test_gpx_export_contains_track_points(self):
        content, _, filename = export_mission(RESULT, "gpx")
        text = content.decode("utf-8")
        self.assertIn('<trkpt lat="55.00000000" lon="37.00000000"', text)
        self.assertNotIn("<ele>", text)
        self.assertTrue(filename.endswith(".gpx"))

    def test_unknown_format_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "kml"):
            export_mission(RESULT, "csv")

    def test_versioned_flight_plan_and_timed_kml(self):
        result = json.loads(json.dumps(RESULT))
        result["mission"]["geometry"] = {
            "type": "MultiPolygon",
            "coordinates": [[[[37.0, 55.0], [37.1, 55.0], [37.1, 55.1], [37.0, 55.0]]]],
        }
        result["plans"][0].update({
            "start_at": "2026-09-20T09:00:00+03:00",
            "completion_at": "2026-09-20T09:10:00+03:00",
        })
        result["plans"][0]["vehicles"][0]["flight_phases"] = [{
            "id": "phase-001", "sequence": 1, "type": "SURVEY", "name": "Съёмочный галс 1",
            "start_at": "2026-09-20T09:00:00+03:00", "end_at": "2026-09-20T09:10:00+03:00",
            "duration_s": 600, "planned_speed_kmh": 72, "start_altitude_m": 120,
            "end_altitude_m": 120, "payload_active": True,
            "geometry": {"type": "LineString", "coordinates": [[37.0, 55.0], [37.1, 55.1]]},
            "trajectory": [
                {"time": "2026-09-20T09:00:00+03:00", "lon": 37.0, "lat": 55.0, "altitude_m": 120},
                {"time": "2026-09-20T09:10:00+03:00", "lon": 37.1, "lat": 55.1, "altitude_m": 120},
            ],
        }]
        plan = build_flight_plan(result)
        self.assertEqual(plan["schema_version"], "1.0")
        self.assertEqual(plan["vehicles"][0]["phases"][0]["type"], "SURVEY")
        content, _, _ = export_mission(result, "kml")
        text = content.decode("utf-8")
        self.assertIn("citymetrics-flight-mission", text)
        self.assertIn("MultiGeometry", text)
        self.assertIn("TimeSpan", text)
        self.assertIn("gx:Track", text)
        self.assertIn("2026-09-20T09:10:00+03:00", text)
        self.assertIn("37.00000000,55.00000000,120.0", text)
        gpx, _, _ = export_mission(result, "gpx")
        self.assertNotIn(b"<ele>", gpx)
        self.assertIn(b"altitude_agl_m", gpx)

    def test_kml_whole_route_uses_changing_agl_altitude(self):
        result = json.loads(json.dumps(RESULT))
        result["plans"][0]["vehicles"][0]["flight_phases"] = [{
            "type": "CLIMB",
            "trajectory": [
                {"time": "2026-09-20T09:00:00+03:00", "lon": 37.0, "lat": 55.0, "altitude_m": 0},
                {"time": "2026-09-20T09:01:00+03:00", "lon": 37.1, "lat": 55.1, "altitude_m": 120},
            ],
            "geometry": {"type": "LineString", "coordinates": [[37.0, 55.0], [37.1, 55.1]]},
        }]
        content, _, _ = export_mission(result, "kml")
        text = content.decode("utf-8")
        self.assertIn("37.00000000,55.00000000,0.0 37.10000000,55.10000000,120.0", text)


if __name__ == "__main__":
    unittest.main()

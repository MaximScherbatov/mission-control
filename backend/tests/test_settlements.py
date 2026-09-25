import tempfile
import unittest
from pathlib import Path

from shapely.geometry import shape
from fastapi.testclient import TestClient

from app import main
from app.settlements import SettlementStore, overpass_query, parse_overpass, tile_bbox, tiles_for_bbox


def relation_payload():
    corners = [
        {"lon": 37.01, "lat": 55.01}, {"lon": 37.05, "lat": 55.01},
        {"lon": 37.05, "lat": 55.05}, {"lon": 37.01, "lat": 55.05},
        {"lon": 37.01, "lat": 55.01},
    ]
    return {"elements": [{
        "type": "relation", "id": 42,
        "tags": {"name": "Тестовый посёлок", "place": "village",
                 "addr:region": "Московская область", "addr:district": "Тестовый район"},
        "members": [{"type": "way", "role": "outer", "geometry": corners}],
    }]}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class FailingClient:
    async def post(self, url, **kwargs):
        raise TimeoutError("Overpass timed out")


class SettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_overpass_relation_and_required_qgis_fields(self):
        polygons, invalid = parse_overpass(relation_payload())
        self.assertEqual(invalid, 0)
        self.assertEqual(len(polygons), 1)
        polygon = polygons[0]
        self.assertEqual(polygon["name"], "Тестовый посёлок")
        self.assertEqual(polygon["region"], "Московская область")
        self.assertEqual(polygon["district"], "Тестовый район")
        self.assertAlmostEqual(shape(polygon["geometry"]).area, 0.0016)
        query = overpass_query((37.0, 55.0, 37.2, 55.2))
        self.assertIn('relation["boundary"="administrative"]', query)
        self.assertIn('relation["boundary"="place"]', query)
        self.assertIn('way["place"', query)

    async def test_unchecked_area_is_not_empty_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettlementStore(Path(directory) / "settlements.sqlite")
            bbox = tile_bbox((185, 275))
            self.assertEqual(store.coverage(bbox)["status"], "NOT_COVERED")
            client = FakeClient(relation_payload())
            synced = await store.ensure(bbox, client)
            self.assertEqual(synced["status"], "COVERED")
            self.assertEqual(len(store.polygons(bbox)), 1)
            self.assertEqual(len(client.calls), 1)
            await store.ensure(bbox, client)
            self.assertEqual(len(client.calls), 1, "Fresh tiles must be reused")

    async def test_invalid_relation_does_not_claim_full_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettlementStore(Path(directory) / "settlements.sqlite")
            payload = relation_payload()
            payload["elements"][0]["members"] = []
            await store.sync_tile((185, 275), FakeClient(payload))
            self.assertNotEqual(store.coverage(tile_bbox((185, 275)))["status"], "COVERED")

    async def test_failed_fetch_is_reported_without_claiming_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettlementStore(Path(directory) / "settlements.sqlite")
            result = await store.ensure(tile_bbox((185, 275)), FailingClient())
            self.assertEqual(result["status"], "NOT_COVERED")
            self.assertEqual(store.last_fetch_error, "TimeoutError")
            self.assertIn("TimeoutError", result["fetch_errors"][0])

    async def test_refresh_deactivates_boundary_removed_from_complete_tile(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettlementStore(Path(directory) / "settlements.sqlite")
            tile = (185, 275)
            await store.sync_tile(tile, FakeClient(relation_payload()))
            self.assertEqual(len(store.polygons(tile_bbox(tile))), 1)
            await store.sync_tile(tile, FakeClient({"elements": []}))
            self.assertEqual(store.coverage(tile_bbox(tile))["status"], "COVERED")
            self.assertEqual(store.polygons(tile_bbox(tile)), [])

    async def test_qgis_import_preserves_requested_fields_without_claiming_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettlementStore(Path(directory) / "settlements.sqlite")
            raw, _ = parse_overpass(relation_payload())
            result = store.import_geojson({"type": "FeatureCollection", "features": [{
                "type": "Feature", "id": 42, "properties": {
                    "name": "Тестовый посёлок", "place": "village",
                    "addr:region": "Московская область", "addr:district": "Тестовый район",
                }, "geometry": raw[0]["geometry"],
            }]}, "qgis-test.geojson")
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["coverage_status"], "NOT_ASSERTED")
            self.assertEqual(store.coverage(tile_bbox((185, 275)))["status"], "NOT_COVERED")
            polygon = store.polygons(tile_bbox((185, 275)))[0]
            self.assertEqual(polygon["region"], "Московская область")
            self.assertEqual(polygon["district"], "Тестовый район")

    async def test_tile_bounds_are_half_open(self):
        self.assertEqual(tiles_for_bbox((37.0, 55.0, 37.2, 55.2)), [(185, 275)])

    async def test_map_endpoint_returns_cached_polygons_without_overpass(self):
        with tempfile.TemporaryDirectory() as directory:
            main.DATABASE_URL = ""
            with TestClient(main.app) as client:
                client.post("/api/auth/login", json={"username": "dispatcher", "password": "dispatcher"})
                store = SettlementStore(Path(directory) / "settlements.sqlite")
                await store.sync_tile((185, 275), FakeClient(relation_payload()))
                main.app.state.settlements = store
                response = client.get("/api/settlements/polygons", params={"bbox": "37,55,37.2,55.2"})
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["coverage"]["status"], "COVERED")
                self.assertEqual(body["features"][0]["properties"]["name"], "Тестовый посёлок")
                self.assertEqual(body["features"][0]["geometry"]["type"], "Polygon")


if __name__ == "__main__":
    unittest.main()

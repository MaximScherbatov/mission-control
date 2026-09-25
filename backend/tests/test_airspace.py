import unittest

from app.airspace import bbox_intersects, filter_zones, geometry_bbox, load_seed_zones, normalize_feature, summary


class AirspaceDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zones = load_seed_zones()

    def test_all_official_datasets_are_loaded(self):
        result = summary(self.zones)
        self.assertEqual(result["total"], 865)
        self.assertEqual(result["counts"]["prohibited"], 781)
        self.assertEqual(result["counts"]["danger"], 62)
        self.assertEqual(result["counts"]["orvd"], 16)
        self.assertEqual(result["counts"]["obstacle"], 6)

    def test_geoscan_primorsky_obstacles_are_not_preloaded(self):
        sources = {zone["source_name"] for zone in self.zones}
        self.assertNotIn("Высотные препятствия Геоскан — Приморский край", sources)

    def test_ids_are_stable_across_reloads(self):
        again = load_seed_zones()
        self.assertEqual([item["id"] for item in self.zones], [item["id"] for item in again])

    def test_bbox_filter_does_not_drop_matching_zone(self):
        zone = self.zones[0]
        matches = filter_zones(self.zones, category=zone["category"], bbox=zone["bbox"])
        self.assertIn(zone["id"], {item["id"] for item in matches})
        self.assertTrue(bbox_intersects(zone["bbox"], zone["bbox"]))

    def test_invalid_wgs84_coordinate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "WGS 84"):
            geometry_bbox({"type": "Point", "coordinates": [500, 55]})

    def test_manual_feature_keeps_original_properties(self):
        feature = {
            "type": "Feature",
            "properties": {"name": "Test", "custom": "preserved"},
            "geometry": {"type": "Polygon", "coordinates": [[[37, 55], [38, 55], [38, 56], [37, 55]]]},
        }
        zone = normalize_feature(feature, "custom", "test", 0, editable=True)
        self.assertEqual(zone["properties"]["custom"], "preserved")
        self.assertTrue(zone["editable"])


if __name__ == "__main__":
    unittest.main()

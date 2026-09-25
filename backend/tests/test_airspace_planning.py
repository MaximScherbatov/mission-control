import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from shapely.geometry import LineString, Polygon

from app.airspace_planning import _altitude_relevant, _time_relevant, avoid_line, prepare_airspace_context


def request(**values):
    defaults = {
        "airspace_check": True,
        "earliest_start": datetime(2026, 9, 20, 8, tzinfo=timezone.utc),
        "max_flight_altitude_m": 120,
        "prohibited_clearance_m": 1,
        "danger_clearance_m": 1,
        "obstacle_clearance_m": 1,
        "settlement_clearance_m": 100,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def zone(category, geometry, **properties):
    minx, miny, maxx, maxy = geometry.bounds
    return {
        "id": f"{category}-1",
        "category": category,
        "code": properties.pop("code", "TEST"),
        "name": properties.pop("name", "Тестовая зона"),
        "geometry": geometry.__geo_interface__,
        "bbox": [minx, miny, maxx, maxy],
        "source_name": "unit-test",
        "vertical_definition": properties.pop("vertical_definition", None),
        "schedule": properties.pop("schedule", None),
        "enabled": True,
        "properties": properties,
    }


class AirspacePlanningTests(unittest.TestCase):
    def test_visibility_graph_builds_safe_detour(self):
        obstacle = Polygon([(4, -1), (6, -1), (6, 1), (4, 1)])
        route, detoured, unresolved = avoid_line(LineString([(0, 0), (10, 0)]), obstacle)
        self.assertTrue(detoured)
        self.assertFalse(unresolved)
        self.assertGreater(route.length, 10)
        self.assertTrue(route.intersection(obstacle.buffer(-0.01)).is_empty)

    def test_detour_keeps_navigation_margin_and_accounts_for_nearby_zone(self):
        first = Polygon([(4, -1), (6, -1), (6, 1), (4, 1)])
        nearby = Polygon([(5, 1.5), (7, 1.5), (7, 3), (5, 3)])
        obstacles = first.union(nearby)
        route, detoured, unresolved = avoid_line(LineString([(0, 0), (10, 0)]), obstacles)
        self.assertTrue(detoured)
        self.assertFalse(unresolved)
        self.assertGreaterEqual(route.distance(obstacles), 2.9)
        self.assertTrue(route.intersection(nearby).is_empty)

    def test_prohibited_overlap_requires_permission_without_silently_cutting_customer_area(self):
        mission = Polygon([(4.2, -0.4), (5.8, -0.4), (5.8, 0.4), (4.2, 0.4)])
        result = prepare_airspace_context(
            request(), [zone("prohibited", Polygon([(4, -1), (6, -1), (6, 1), (4, 1)]))],
            mission, lambda x, y, z=None: (x, y), mission, [-2, -2, 12, 2],
        )
        self.assertEqual(result["assessment"]["status"], "permission_required")
        self.assertEqual(len(result["assessment"]["conflicts"]), 1)
        self.assertIsNone(result["coverage_obstacles"])
        self.assertIsNotNone(result["transit_obstacles"])

    def test_external_prohibited_zone_is_used_for_transit_avoidance(self):
        mission = Polygon([(8, -0.5), (9, -0.5), (9, 0.5), (8, 0.5)])
        result = prepare_airspace_context(
            request(), [zone("prohibited", Polygon([(4, -1), (6, -1), (6, 1), (4, 1)]))],
            mission, lambda x, y, z=None: (x, y), mission, [-2, -2, 12, 2],
        )
        self.assertEqual(result["assessment"]["status"], "clear")
        self.assertIsNotNone(result["transit_obstacles"])

    def test_settlement_clearance_is_a_transit_obstacle_and_blocks_coverage(self):
        settlement = zone("settlement", Polygon([(4, -1), (6, -1), (6, 1), (4, 1)]), name="Посёлок")
        outside = Polygon([(8, -0.5), (9, -0.5), (9, 0.5), (8, 0.5)])
        context = prepare_airspace_context(
            request(settlement_clearance_m=2), [settlement], outside,
            lambda x, y, z=None: (x, y), outside, [-2, -2, 12, 2],
        )
        self.assertEqual(context["assessment"]["clearances_m"]["settlement"], 2)
        self.assertIsNone(context["coverage_obstacles"])
        self.assertTrue(context["transit_obstacles"].contains(LineString([(4, 0), (6, 0)])))
        overlapping = Polygon([(7, -.5), (8, -.5), (8, .5), (7, .5)])
        blocked = prepare_airspace_context(
            request(settlement_clearance_m=2), [settlement], overlapping,
            lambda x, y, z=None: (x, y), overlapping, [-2, -2, 12, 2],
        )
        self.assertEqual(blocked["assessment"]["status"], "permission_required")
        self.assertEqual(blocked["assessment"]["conflicts"][0]["name"], "Посёлок")

    def test_expired_zone_does_not_block_planning_date(self):
        mission = Polygon([(4.2, -0.4), (5.8, -0.4), (5.8, 0.4), (4.2, 0.4)])
        expired = zone(
            "danger", Polygon([(4, -1), (6, -1), (6, 1), (4, 1)]),
            official_valid_until="2020-01-01",
        )
        result = prepare_airspace_context(
            request(), [expired], mission, lambda x, y, z=None: (x, y), mission, [-2, -2, 12, 2],
        )
        self.assertEqual(result["assessment"]["status"], "clear")
        self.assertEqual(result["assessment"]["conflicts"], [])

    def test_zone_activating_during_mission_is_not_ignored(self):
        mission = Polygon([(4.2, -0.4), (5.8, -0.4), (5.8, 0.4), (4.2, 0.4)])
        future = zone("prohibited", mission, effective_from="2026-09-20T10:00:00+00:00")
        result = prepare_airspace_context(
            request(deadline=datetime(2026, 9, 20, 12, tzinfo=timezone.utc)), [future],
            mission, lambda x, y, z=None: (x, y), mission, [-2, -2, 12, 2],
        )
        self.assertEqual(result["assessment"]["status"], "permission_required")
        self.assertFalse(_time_relevant(future, datetime(2026, 9, 20, 8, tzinfo=timezone.utc))[0])

    def test_climb_passes_through_vertically_limited_zone(self):
        relevant, basis = _altitude_relevant({"properties": {"fpln_altitude_range": [0, 80]}}, 120)
        self.assertTrue(relevant)
        self.assertEqual(basis, "structured_range_climb_to_cruise")


if __name__ == "__main__":
    unittest.main()

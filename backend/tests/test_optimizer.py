import math
import unittest

from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import transform
from pyproj import Transformer

from app.airspace import load_seed_zones
from app.models import MissionRequest
from app.catalog import UAVS
from app.optimizer import _best_pair, _flight_connector, _order_wide_turn_strips, _planning_turn_radius, _runway_direction, _select_strip_sequence, _sweep_lines, _wind_axis_metrics, optimize


AREA = {
    "type": "Polygon",
    "coordinates": [[
        [37.37, 55.84],
        [37.43, 55.84],
        [37.44, 55.80],
        [37.36, 55.80],
        [37.37, 55.84],
    ]],
}
SMALL_AREA = {
    "type": "Polygon",
    "coordinates": [[
        [37.39, 55.82], [37.41, 55.82], [37.41, 55.81],
        [37.39, 55.81], [37.39, 55.82],
    ]],
}


class OptimizerTest(unittest.TestCase):
    def test_full_sweep_can_start_at_opposite_edge(self):
        strips = [LineString([(9000, 0), (9000, 1000)]), LineString([(1000, 0), (1000, 2000)])]
        selected = _select_strip_sequence(strips, (0, 0), None, 0)
        self.assertEqual(tuple(selected[0].coords[0]), (1000.0, 0.0))
    def test_active_prohibited_zone_inside_customer_area_stops_plan(self):
        zone = next(zone for zone in load_seed_zones() if zone["code"] == "UUP53")
        with self.assertRaisesRegex(ValueError, "UUP53"):
            optimize(MissionRequest(area=AREA), [zone])

    def test_fixed_wing_transit_curves_around_protected_area(self):
        protected = Polygon([(4000, -1000), (6000, -1000), (6000, 1000), (4000, 1000)])
        route, detoured, unresolved = _flight_connector(
            (0, 0), (1, 0), (10000, 0), (1, 0), 200, protected,
        )
        self.assertTrue(detoured)
        self.assertFalse(unresolved)
        self.assertGreaterEqual(route.distance(protected), 3)
        self.assertGreater(route.length, 10000)

    def test_multirotor_transit_uses_straight_reference_legs(self):
        protected = Polygon([(4000, -1000), (6000, -1000), (6000, 1000), (4000, 1000)])
        direct, detoured, unresolved = _flight_connector(
            (0, 0), (0, 1), (10000, 0), (0, -1), 0, None,
        )
        self.assertEqual(list(direct.coords), [(0.0, 0.0), (10000.0, 0.0)])
        self.assertFalse(detoured)
        self.assertFalse(unresolved)
        bypass, detoured, unresolved = _flight_connector(
            (0, 0), (0, 1), (10000, 0), (0, -1), 0, protected,
        )
        self.assertTrue(detoured)
        self.assertFalse(unresolved)
        self.assertGreater(len(bypass.coords), 2)
        self.assertGreater(bypass.distance(protected), 0)

    def test_external_prohibited_zone_is_avoided_by_every_plan(self):
        forbidden_geometry = {
            "type": "Polygon", "coordinates": [[
                [37.34, 55.84], [37.36, 55.84], [37.36, 55.86],
                [37.34, 55.86], [37.34, 55.84],
            ]],
        }
        zone = {
            "id": "test-transit", "category": "prohibited", "code": "TEST",
            "name": "Transit barrier", "geometry": forbidden_geometry,
            "bbox": [37.34, 55.84, 37.36, 55.86], "enabled": True,
            "source_name": "unit-test", "properties": {},
        }
        runway = {
            "id": "base-north", "name": "ВПП Север", "lon": 37.305, "lat": 55.878,
            "kind": "runway", "runway_length_m": 460, "heading_deg": 82,
            "supports": ["fixed_wing", "multirotor", "vtol"], "status": "open",
        }
        result = optimize(MissionRequest(
            area=SMALL_AREA, launch_point=(37.305, 55.878), launch_site=runway,
            payload_id="payload-sony-61", max_uavs=1,
        ), [zone])
        protected = shape(forbidden_geometry)
        self.assertTrue(result["plans"])
        for plan in result["plans"]:
            self.assertEqual(plan["airspace_avoidance"]["unresolved"], 0)
            self.assertGreater(plan["airspace_avoidance"]["detours"], 0)
            for vehicle in plan["vehicles"]:
                self.assertFalse(shape(vehicle["route"]).intersects(protected))

    def test_height_obstacle_clearance_is_checked_on_complete_route(self):
        point = [37.35, 55.85]
        zone = {
            "id": "height-test", "category": "obstacle", "code": "MAST",
            "name": "Height obstacle", "geometry": {"type": "Point", "coordinates": point},
            "bbox": [*point, *point], "enabled": True,
            "source_name": "unit-test", "properties": {},
        }
        runway = {
            "id": "base-north", "name": "ВПП Север", "lon": 37.305, "lat": 55.878,
            "kind": "runway", "runway_length_m": 460, "heading_deg": 82,
            "supports": ["fixed_wing", "multirotor", "vtol"], "status": "open",
        }
        result = optimize(MissionRequest(
            area=SMALL_AREA, launch_point=(37.305, 55.878), launch_site=runway,
            payload_id="payload-sony-61", max_uavs=1,
        ), [zone])
        project = Transformer.from_crs("EPSG:4326", "EPSG:32637", always_xy=True).transform
        protected = transform(project, Point(*point)).buffer(50)
        for plan in result["plans"]:
            for vehicle in plan["vehicles"]:
                route = transform(project, shape(vehicle["route"]))
                self.assertGreaterEqual(route.distance(protected), 1)

    def test_runway_direction_prefers_headwind_when_material(self):
        axis = (0.0, 1.0)
        self.assertEqual(_runway_direction((0,0),(0,-1000),axis,0,5,0), axis)
        self.assertEqual(_runway_direction((0,0),(0,1000),axis,0,5,180), (0.0,-1.0))
        self.assertEqual(_runway_direction((0,0),(0,-1000),axis,0,.2,90), (0.0,-1.0))

    def test_interleaved_order_makes_room_for_fixed_wing_turns(self):
        strips = [LineString([(0, index * 60), (1000, index * 60)]) for index in range(12)]
        ordered = _order_wide_turn_strips(strips, 60, 110)
        rows = [round(line.coords[0][1] / 60) for line in ordered]
        self.assertEqual(sorted(rows), list(range(12)))
        self.assertEqual(rows[:3], [0, 4, 8])
        self.assertGreaterEqual(sum(abs(b - a) >= 4 for a, b in zip(rows, rows[1:])), 8)

    def test_demo_compares_repeat_sorties_with_parallel_aircraft(self):
        demo_area = {"type": "Polygon", "coordinates": [[
            [37.21, 55.97], [37.28, 55.97], [37.29, 55.93],
            [37.21, 55.93], [37.21, 55.97],
        ]]}
        result = optimize(MissionRequest(
            area=demo_area, launch_point=(37.25, 55.975),
            launch_site={
                'id':'base-demo-ligachevo','name':'Учебная ВПП Лигачёво',
                'lon':37.25,'lat':55.975,'kind':'runway',
                'runway_length_m':460,'heading_deg':180,
                'supports':['fixed_wing','multirotor','vtol'],'status':'open',
            },
            survey_type="rgb", payload_id="payload-sony-61",
            gsd_cm_px=5, max_flight_altitude_m=150, wind_direction_deg=180,
        ))
        alternatives = {item["uav_count"]: item for item in result["fleet_comparison"]}
        self.assertGreaterEqual(alternatives[1]["sorties"], 1)
        self.assertEqual(alternatives[3]["sorties"], 3)
        self.assertEqual(alternatives[3]["vehicle_sorties"], [1, 1, 1])
        self.assertEqual(min(item["total_flight_time_min"] for item in alternatives.values()), next(plan for plan in result['plans'] if plan['id'] == 'economy')['total_flight_time_min'])
        self.assertLess(alternatives[3]["duration_min"], alternatives[1]["duration_min"])
        economy = next(plan for plan in result["plans"] if plan["id"] == "economy")
        fast = next(plan for plan in result["plans"] if plan["id"] == "fast")
        self.assertLessEqual(fast['duration_min'], economy['duration_min'])
        self.assertLessEqual(economy['total_flight_time_min'], fast['total_flight_time_min'])
        vehicle = economy["vehicles"][0]
        home = vehicle["route"]["coordinates"][0]
        self.assertGreaterEqual(vehicle["route"]["coordinates"].count(home), vehicle["sorties"] + 1)
        self.assertEqual(vehicle['runway_heading_deg'], 180)
        self.assertEqual(vehicle['departure_heading_deg'], 180)
        self.assertEqual(vehicle['runway_length_m'], 460)
        route = vehicle['route']['coordinates']
        self.assertLess(route[1][1], route[0][1])
        self.assertAlmostEqual(route[1][0], route[0][0], delta=1e-5)
        self.assertGreater(route[-2][1], route[-1][1])
        self.assertAlmostEqual(route[-2][0], route[-1][0], delta=1e-5)
        climb = next(phase for phase in vehicle['flight_phases'] if phase['type'] == 'CLIMB')
        landing = next(phase for phase in vehicle['flight_phases'] if phase['type'] == 'LANDING')
        self.assertEqual(climb['geometry']['type'], 'LineString')
        self.assertEqual(landing['geometry']['type'], 'LineString')
        self.assertAlmostEqual(climb['geometry']['coordinates'][0][1], home[1], places=5)
        self.assertAlmostEqual(landing['geometry']['coordinates'][-1][1], home[1], places=5)

    def test_turn_radius_uses_survey_airspeed_and_bank_limit(self):
        uav = next(item for item in UAVS if item['id'] == 'uav-geoscan-201-01')
        pair = _best_pair(uav, MissionRequest(area=AREA, payload_id='payload-sony-61'))
        self.assertIsNotNone(pair)
        expected = max(95, (pair.speed_kmh / 3.6) ** 2 / (9.80665 * math.tan(math.radians(25))))
        self.assertAlmostEqual(_planning_turn_radius(pair), expected)

    def test_distant_launch_point_is_rejected_before_route_search(self):
        with self.assertRaisesRegex(ValueError, "радиус полёта с возвратом"):
            optimize(MissionRequest(area=AREA, launch_point=(20.0, 55.82)))

    def test_remote_object_cannot_silently_create_mobile_launch_site(self):
        remote_area = {"type": "Polygon", "coordinates": [[
            [50.0, 55.8], [50.01, 55.8], [50.01, 55.79], [50.0, 55.79], [50.0, 55.8],
        ]]}
        with self.assertRaisesRegex(ValueError, "радиус полёта с возвратом"):
            optimize(MissionRequest(area=remote_area))

        result = optimize(MissionRequest(
            area=remote_area, launch_point=(50.005, 55.795), control_link_mode="external",
        ))
        for plan in result["plans"]:
            self.assertTrue(plan["deployment"]["required"])
            self.assertGreater(plan["deployment"]["max_distance_km"], 700)
            self.assertFalse(plan["deployment"]["cost_included"])

    def test_direct_radio_range_is_a_hard_planning_limit(self):
        with self.assertRaisesRegex(ValueError, "радиосвяз"):
            optimize(MissionRequest(area=AREA, radio_equipment_range_km=1))

    def test_external_link_does_not_claim_verified_coverage(self):
        result = optimize(MissionRequest(area=AREA, control_link_mode="external"))
        for plan in result["plans"]:
            self.assertEqual(plan["link_assessment"]["status"], "coverage_unverified")
            self.assertFalse(plan["link_assessment"]["coverage_verified"])

    def test_builds_three_explainable_rgb_plans(self):
        result = optimize(MissionRequest(area=AREA, survey_type="rgb", gsd_cm_px=5))
        self.assertEqual(result["recommended_plan_id"], "economy")
        self.assertEqual({plan["id"] for plan in result["plans"]}, {"fast", "economy", "safe"})
        for plan in result["plans"]:
            self.assertGreater(plan["duration_min"], 0)
            self.assertGreater(plan["cost_rub"], 0)
            self.assertTrue(plan["vehicles"])
            self.assertEqual(plan["coverage_percent"], 100.0)
            self.assertTrue(plan["explanations"])
            self.assertGreaterEqual(plan["flight_direction_deg"], 0)
            self.assertLess(plan["flight_direction_deg"], 180)
            self.assertEqual(plan["wind_analysis"]["wind_direction_from_deg"], 315)
            for vehicle in plan["vehicles"]:
                self.assertEqual(vehicle["route"]["type"], "LineString")
                self.assertEqual(vehicle["coverage_route"]["type"], "MultiLineString")
                self.assertEqual(vehicle["transit_route"]["type"], "MultiLineString")
                self.assertTrue(vehicle["flight_phases"])
                self.assertEqual(vehicle["flight_phases"][0]["sequence"], 1)
                self.assertTrue(all(phase["start_at"] < phase["end_at"] for phase in vehicle["flight_phases"]))
                self.assertTrue(any(phase["type"] == "SURVEY" and phase["payload_active"] for phase in vehicle["flight_phases"]))
                self.assertGreater(vehicle["strip_count"], 0)
                self.assertGreaterEqual(vehicle["reserve_percent"], 20)
                interior_turns = vehicle["transit_route"]["coordinates"][1:-1]
                if vehicle["strip_count"] > 1 and vehicle["sorties"] == 1:
                    self.assertTrue(interior_turns)
                    self.assertTrue(all(len(turn) > 2 for turn in interior_turns))
                if vehicle["sorties"] > 1:
                    self.assertEqual(
                        sum(phase["type"] == "CLIMB" for phase in vehicle["flight_phases"]),
                        vehicle["sorties"],
                    )
                    self.assertEqual(
                        sum(phase["type"] == "LANDING" for phase in vehicle["flight_phases"]),
                        vehicle["sorties"],
                    )
                    self.assertEqual(
                        sum(phase["type"] == "TURNAROUND" for phase in vehicle["flight_phases"]),
                        vehicle["sorties"] - 1,
                    )
        economy = next(plan for plan in result["plans"] if plan["id"] == "economy")
        safe = next(plan for plan in result["plans"] if plan["id"] == "safe")
        self.assertEqual(economy["uav_count"], 1)
        self.assertEqual(safe["uav_count"], 1)
        self.assertEqual(economy["sorties"], sum(vehicle["sorties"] for vehicle in economy["vehicles"]))
        self.assertIn("Минимальный суммарный налёт", economy["selection_reason"])
        self.assertEqual(economy["total_flight_time_min"], min(item["total_flight_time_min"] for item in result["fleet_comparison"]))
        self.assertEqual({item["uav_count"] for item in result["fleet_comparison"]}, {1, 2, 3, 4})
        for vehicle in economy["vehicles"]:
            self.assertLessEqual(vehicle["max_sortie_min"], vehicle["usable_endurance_min"])

    def test_ir_selects_multispectral_payload(self):
        result = optimize(MissionRequest(area=SMALL_AREA, result_type="thermal_map", survey_type="ir", gsd_cm_px=8))
        optics = {vehicle["optic_name"] for plan in result["plans"] for vehicle in plan["vehicles"]}
        self.assertEqual(optics, {"FLIR Vue Pro R · 640 / 13 мм (пример)"})

    def test_camera_geometry_is_exposed_and_reproducible(self):
        request = MissionRequest(
            area=AREA, survey_type="rgb", payload_id="payload-sony-61",
            gsd_cm_px=5, max_flight_altitude_m=150,
            side_overlap=0.60, forward_overlap=0.70,
        )
        result = optimize(request)
        vehicle = result["plans"][0]["vehicles"][0]
        engineering = vehicle["engineering"]
        expected_gsd = 150 * 35.7 / 35 / 9504 * 100
        self.assertAlmostEqual(engineering["achieved_gsd_cm_px"], expected_gsd, places=2)
        self.assertAlmostEqual(engineering["line_spacing_m"], engineering["footprint_width_m"] * 0.40, delta=0.1)
        self.assertLessEqual(engineering["required_fps"], engineering["camera_fps"])
        self.assertEqual(result["engineering"]["method"], "central_projection")

    def test_explicitly_incompatible_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Нет БВС"):
            optimize(MissionRequest(area=AREA, survey_type="rgb", payload_id="payload-agm-lidar"))

    def test_rejects_malformed_polygon(self):
        with self.assertRaisesRegex(ValueError, "координат"):
            optimize(MissionRequest(area={"type": "Polygon", "coordinates": [[1.0, 2.0, 3.0]]}))

    def test_all_catalog_results_have_a_feasible_plan(self):
        demonstration_area = {"type": "Polygon", "coordinates": [[
            [37.457, 55.787], [37.480, 55.787], [37.480, 55.777], [37.457, 55.777], [37.457, 55.787],
        ]]}
        products = {
            "orthophoto": ("rgb", 5), "thermal_map": ("ir", 8), "point_cloud": ("lidar", 5),
            "magnetic_map": ("geophysical", 5), "powerline_report": ("rgb", 5),
            "digital_twin": ("rgb", 3), "ndvi": ("multispectral", 8),
        }
        for result_type, (survey_type, gsd) in products.items():
            with self.subTest(result_type=result_type):
                result = optimize(MissionRequest(area=demonstration_area, result_type=result_type, result_types=[result_type], survey_type=survey_type, gsd_cm_px=gsd))
                self.assertTrue(result["plans"])

    def test_linear_heat_route_builds_longitudinal_corridor(self):
        line = {"type": "LineString", "coordinates": [[37.457, 55.787], [37.470, 55.783], [37.480, 55.777]]}
        result = optimize(MissionRequest(area=line, result_type="thermal_map", result_types=["thermal_map"], survey_type="ir", gsd_cm_px=8, corridor_width_m=60))
        self.assertEqual(result["mission"]["geometry_mode"], "corridor")
        self.assertGreater(result["mission"]["route_length_km"], 1)
        self.assertTrue(result["plans"])

    def test_wind_vector_is_projected_for_both_galley_directions(self):
        # Wind from north travels south. A northbound pass sees a headwind and
        # the reverse pass sees an equal tailwind; an east-west pass sees only
        # the crosswind component and requires a crab angle.
        northbound, southbound, crosswind = _wind_axis_metrics(90, 20, 8, 0)
        self.assertAlmostEqual(northbound, 12, places=4)
        self.assertAlmostEqual(southbound, 28, places=4)
        self.assertAlmostEqual(crosswind, 0, places=4)

        eastbound, westbound, crosswind = _wind_axis_metrics(0, 20, 8, 0)
        self.assertAlmostEqual(eastbound, (20 ** 2 - 8 ** 2) ** 0.5, places=4)
        self.assertAlmostEqual(westbound, eastbound, places=4)
        self.assertAlmostEqual(crosswind, 8, places=4)

    def test_launch_wind_does_not_rotate_equal_geometry(self):
        square = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        _, north_wind_angle = _sweep_lines(
            square, 100, airspeed_mps=20, wind_speed_mps=8, wind_direction_deg=0,
        )
        _, east_wind_angle = _sweep_lines(
            square, 100, airspeed_mps=20, wind_speed_mps=8, wind_direction_deg=90,
        )
        self.assertEqual(north_wind_angle, east_wind_angle)


if __name__ == "__main__":
    unittest.main()

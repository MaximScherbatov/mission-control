"""Regression checks for the route shown near Firsanovka and Krasnogorsk."""

import math
import unittest
from unittest.mock import patch

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from app.airspace import load_seed_zones
from app.catalog import UAVS
from app.models import MissionRequest
from app.optimizer import optimize


FIRSANOVKA_RUNWAY = {
    "id": "base-north",
    "name": "ВПП Север",
    "lon": 37.305,
    "lat": 55.878,
    "kind": "runway",
    "runway_length_m": 460,
    "heading_deg": 82,
    "supports": ["fixed_wing", "multirotor", "vtol"],
    "status": "open",
}
KRASNOGORSK_AREA = {
    "type": "Polygon",
    "coordinates": [[
        [37.37, 55.84], [37.43, 55.84], [37.44, 55.80],
        [37.36, 55.80], [37.37, 55.84],
    ]],
}
TO_METRES = Transformer.from_crs("EPSG:4326", "EPSG:32637", always_xy=True).transform


def _metric_points(coordinates):
    return [TO_METRES(*point[:2]) for point in coordinates]


def _heading(start, end):
    return math.atan2(end[1] - start[1], end[0] - start[0])


def _heading_change(current, following):
    return abs(math.degrees((following - current + math.pi) % (2 * math.pi) - math.pi))


def _course_changes(coordinates):
    points = _metric_points(coordinates)
    headings = [
        _heading(start, end)
        for start, end in zip(points, points[1:])
        if math.dist(start, end) >= 0.5
    ]
    return [
        _heading_change(current, following)
        for current, following in zip(headings, headings[1:])
    ]


def _join_angle(left_coordinates, right_coordinates):
    left, right = _metric_points(left_coordinates), _metric_points(right_coordinates)
    incoming = next(
        _heading(start, end) for start, end in reversed(list(zip(left, left[1:])))
        if math.dist(start, end) >= 0.5
    )
    outgoing = next(
        _heading(start, end) for start, end in zip(right, right[1:])
        if math.dist(start, end) >= 0.5
    )
    return _heading_change(incoming, outgoing)


def _distance_to_first_turn(coordinates):
    points = _metric_points(coordinates)
    runway_heading = _heading(points[0], points[1])
    distance = 0.0
    for start, end in zip(points, points[1:]):
        heading = _heading(start, end)
        if _heading_change(runway_heading, heading) >= 5:
            return distance
        distance += math.dist(start, end)
    return math.inf


def _turn_radii(coordinates):
    points = _metric_points(coordinates)
    for first, middle, last in zip(points, points[1:], points[2:]):
        incoming = math.dist(first, middle)
        outgoing = math.dist(middle, last)
        if min(incoming, outgoing) < 1.0:
            continue
        angle = _heading_change(_heading(first, middle), _heading(middle, last))
        if angle < 0.5:  # Near-straight triples amplify projection noise.
            continue
        chord = math.dist(first, last)
        area_twice = abs(
            (middle[0] - first[0]) * (last[1] - first[1])
            - (middle[1] - first[1]) * (last[0] - first[0])
        )
        yield incoming * outgoing * chord / (2 * area_twice)


class RouteGeometryTest(unittest.TestCase):
    def test_moscow_prohibited_zones_do_not_force_giant_transit(self):
        area = {"type": "Polygon", "coordinates": [[
            [37.34, 55.62], [37.365, 55.62], [37.365, 55.64],
            [37.34, 55.64], [37.34, 55.62],
        ]]}
        zones = [zone for zone in load_seed_zones() if zone.get("code") in {"UUP52", "UUP53"}]
        aircraft = next(uav for uav in UAVS if uav["id"] == "uav-geoscan-201-01")
        with patch("app.optimizer.UAVS", [aircraft]):
            result = optimize(MissionRequest(
                area=area, launch_point=(FIRSANOVKA_RUNWAY["lon"], FIRSANOVKA_RUNWAY["lat"]),
                launch_site=FIRSANOVKA_RUNWAY, payload_id="payload-sony-61", max_uavs=1,
            ), zones)
        vehicle = next(plan for plan in result["plans"] if plan["id"] == "economy")["vehicles"][0]
        transit_km = sum(transform(TO_METRES, shape(phase["geometry"])).length / 1000
                         for phase in vehicle["flight_phases"]
                         if phase["type"] in {"CLIMB", "OUTBOUND", "INBOUND", "LANDING"})
        start = TO_METRES(FIRSANOVKA_RUNWAY["lon"], FIRSANOVKA_RUNWAY["lat"])
        destination = TO_METRES(37.3525, 55.63)
        direct_roundtrip_km = 2 * math.dist(start, destination) / 1000
        self.assertLess(transit_km, direct_roundtrip_km * 1.12)
        for zone in zones:
            self.assertFalse(shape(vehicle["route"]).crosses(shape(zone["geometry"])))

    def test_firsanovka_fixed_wing_departure_and_return_are_smooth(self):
        result = optimize(MissionRequest(
            area=KRASNOGORSK_AREA,
            launch_point=(FIRSANOVKA_RUNWAY["lon"], FIRSANOVKA_RUNWAY["lat"]),
            launch_site=FIRSANOVKA_RUNWAY,
            survey_type="rgb",
            payload_id="payload-sony-61",
            gsd_cm_px=5,
            max_flight_altitude_m=150,
        ))
        vehicles = [
            vehicle for plan in result["plans"] for vehicle in plan["vehicles"]
            if vehicle["uav_type"] == "fixed_wing"
        ]
        self.assertTrue(vehicles, "The scenario must include an airplane route")

        for vehicle in vehicles:
            with self.subTest(uav=vehicle["uav_id"]):
                phases = vehicle["flight_phases"]
                airborne = [phase for phase in phases if phase["type"] not in {"HOLD", "TURNAROUND"}]
                self.assertEqual(airborne[0]["type"], "CLIMB")
                self.assertEqual(airborne[1]["type"], "OUTBOUND")
                self.assertEqual(airborne[-2]["type"], "INBOUND")
                self.assertEqual(airborne[-1]["type"], "LANDING")

                climbs = [phase for phase in airborne if phase["type"] == "CLIMB"]
                landings = [phase for phase in airborne if phase["type"] == "LANDING"]
                self.assertEqual(len(climbs), vehicle["sorties"])
                self.assertEqual(len(landings), vehicle["sorties"])
                for climb, landing in zip(climbs, landings):
                    departure = climb["geometry"]["coordinates"]
                    approach = landing["geometry"]["coordinates"]
                    self.assertGreater(len(departure), 3)
                    self.assertGreater(len(approach), 3)

                    # A short straight runway portion is followed by a curve
                    # while the aircraft is still in CLIMB. The reciprocal
                    # LANDING phase starts on a curve and ends straight.
                    departure_points = _metric_points(departure)
                    approach_points = _metric_points(approach)
                    self.assertGreater(math.dist(*departure_points[:2]), 50)
                    self.assertLess(math.dist(*departure_points[:2]), vehicle["runway_length_m"])
                    self.assertGreater(math.dist(*approach_points[-2:]), 50)
                    self.assertLess(math.dist(*approach_points[-2:]), vehicle["runway_length_m"])
                    self.assertLess(_distance_to_first_turn(departure), 800)
                    self.assertLess(_distance_to_first_turn(list(reversed(approach))), 1000)

                # Takeoff, transit, survey and approach are one continuous
                # aircraft path; testing each phase alone misses right-angle
                # bends precisely where the phase boundaries meet.
                for first, second in zip(airborne, airborne[1:]):
                    if first["type"] == "LANDING":
                        continue  # Separate sorties meet at the runway.
                    left, right = first["geometry"], second["geometry"]
                    self.assertEqual(left["type"], "LineString")
                    self.assertEqual(right["type"], "LineString")
                    self.assertLess(
                        math.dist(*_metric_points([left["coordinates"][-1], right["coordinates"][0]])), 0.5,
                        f"Gap between {first['type']} and {second['type']}",
                    )
                    self.assertLess(
                        _join_angle(left["coordinates"], right["coordinates"]), 22,
                        f"Sharp turn between {first['type']} and {second['type']}",
                    )

                # The map and exports use vehicle.route. Check every displayed
                # sortie separately because the next takeoff follows ground
                # service and may choose a different runway direction.
                route = vehicle["route"]["coordinates"]
                metric_route = _metric_points(route)
                home = metric_route[0]
                home_indices = [0] + [
                    index for index, point in enumerate(metric_route[1:], start=1)
                    if math.dist(point, home) < 0.5
                ]
                self.assertEqual(len(home_indices) - 1, vehicle["sorties"])
                for start, end in zip(home_indices, home_indices[1:]):
                    sortie = route[start:end + 1]
                    self.assertGreater(len(sortie), 20)
                    self.assertLess(max(_course_changes(sortie)), 22)
                    radii = list(_turn_radii(sortie))
                    self.assertTrue(radii, "Expected sampled flight arcs")
                    self.assertGreaterEqual(
                        min(radii), 0.95 * vehicle["turn_radius_m"],
                        "Displayed arc tighter than the aircraft's planning turn radius",
                    )

                # Smooth arcs must not create a giant loop merely to hide a
                # corner. These two long transit legs need modest detours.
                for phase in airborne:
                    if phase["type"] not in {"OUTBOUND", "INBOUND"}:
                        continue
                    points = _metric_points(phase["geometry"]["coordinates"])
                    direct = math.dist(points[0], points[-1])
                    flown = sum(math.dist(start, end) for start, end in zip(points, points[1:]))
                    self.assertGreater(len(points), 4)
                    self.assertLess(flown / direct, 1.2)

    def test_multirotor_repeat_sorties_join_transit_and_survey_by_waypoints(self):
        small_area = {
            "type": "Polygon",
            "coordinates": [[
                [37.457, 55.787], [37.480, 55.787], [37.480, 55.777],
                [37.457, 55.777], [37.457, 55.787],
            ]],
        }
        result = optimize(MissionRequest(
            area=small_area,
            launch_point=(37.468, 55.782),
            result_type="point_cloud",
            survey_type="lidar",
            payload_id="payload-agm-lidar",
            max_uavs=1,
        ))
        vehicle = next(plan for plan in result["plans"] if plan["id"] == "economy")["vehicles"][0]
        self.assertEqual(vehicle["uav_type"], "multirotor")
        self.assertGreater(vehicle["sorties"], 1)

        sortie_surveys = []
        current_surveys = []
        for phase in vehicle["flight_phases"]:
            if phase["type"] == "SURVEY":
                current_surveys.append(phase)
            elif phase["type"] == "LANDING":
                self.assertTrue(current_surveys)
                sortie_surveys.append((current_surveys[0], current_surveys[-1]))
                current_surveys = []
        self.assertEqual(len(sortie_surveys), vehicle["sorties"])

        # A short trip can be entirely inside the CLIMB or LANDING time slice,
        # so OUTBOUND/INBOUND need not be separate phases. The displayed route
        # still contains both external transitions for every sortie.
        route = vehicle["route"]["coordinates"]
        metric_route = _metric_points(route)
        home = metric_route[0]
        home_indices = [0] + [
            index for index, point in enumerate(metric_route[1:], start=1)
            if math.dist(point, home) < 0.5
        ]
        self.assertEqual(len(home_indices) - 1, vehicle["sorties"])
        for (start, end), (first_survey, last_survey) in zip(
            zip(home_indices, home_indices[1:]), sortie_surveys
        ):
            sortie = route[start:end + 1]
            metric_sortie = _metric_points(sortie)
            first_start = _metric_points(first_survey["geometry"]["coordinates"])[0]
            last_end = _metric_points(last_survey["geometry"]["coordinates"])[-1]
            first_index = next(
                index for index, point in enumerate(metric_sortie)
                if math.dist(point, first_start) < 0.5
            )
            last_index = max(
                index for index, point in enumerate(metric_sortie)
                if math.dist(point, last_end) < 0.5
            )
            outbound = sortie[:first_index + 1]
            inbound = sortie[last_index:]
            self.assertGreaterEqual(len(outbound), 2)
            self.assertGreaterEqual(len(inbound), 2)
            self.assertLess(math.dist(_metric_points(outbound)[-1], first_start), 0.5)
            self.assertLess(math.dist(_metric_points(inbound)[0], last_end), 0.5)


if __name__ == "__main__":
    unittest.main()

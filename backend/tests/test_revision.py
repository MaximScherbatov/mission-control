import math
import unittest
from bisect import bisect_right
from app.catalog import UAVS
from app.models import MissionRequest
from app.optimizer import _best_pair, _sweep_lines, optimize
from app.simulation import (
    GROUND_SERVICE_SECONDS,
    SITES,
    _distance_at_time,
    _motion_timeline,
    sample,
    snapshot,
    track,
    route_parts,
)
from shapely.geometry import Polygon

AREA = {'type':'Polygon','coordinates':[[[37.457,55.787],[37.48,55.787],[37.48,55.777],[37.457,55.777],[37.457,55.787]]]}

class RevisionTests(unittest.TestCase):
    def pair(self, **options):
        return _best_pair(UAVS[0],MissionRequest(area=AREA,payload_id='payload-sony-61',**options))

    def test_gsd_changes_height_before_cap(self):
        a,b = self.pair(gsd_cm_px=.8), self.pair(gsd_cm_px=1.6)
        self.assertAlmostEqual(b.altitude_m, a.altitude_m*2)
        self.assertAlmostEqual(b.swath_m,a.swath_m*2)

    def test_gsd_cap_is_real_not_ignored_input(self):
        a,b = self.pair(gsd_cm_px=5),self.pair(gsd_cm_px=9)
        self.assertEqual(a.altitude_m,150)
        self.assertEqual(a.achieved_gsd_cm_px,b.achieved_gsd_cm_px)

    def test_both_overlaps_affect_geometry(self):
        a,b=self.pair(side_overlap=.5,forward_overlap=.6),self.pair(side_overlap=.75,forward_overlap=.8)
        self.assertAlmostEqual(b.line_spacing_m,a.line_spacing_m/2)
        self.assertAlmostEqual(b.trigger_spacing_m,a.trigger_spacing_m/2)
        self.assertAlmostEqual(b.required_fps,a.required_fps*2)

    def test_small_polygon_still_has_strip(self):
        lines,_=_sweep_lines(Polygon([(0,0),(10,0),(10,10),(0,10)]),50)
        self.assertEqual(len(lines),1)

    def test_self_intersection_rejected(self):
        with self.assertRaisesRegex(ValueError,'самопересекается'):
            optimize(MissionRequest(area={'type':'Polygon','coordinates':[[[37,55],[37.01,55.01],[37,55.01],[37.01,55],[37,55]]]}))

    def test_phases_and_cost_reconcile(self):
        result=optimize(MissionRequest(area=AREA,payload_id='payload-sony-61'))
        by_id={p['id']:p for p in result['plans']}
        self.assertLessEqual(by_id['economy']['cost_rub'],by_id['fast']['cost_rub'])
        self.assertLessEqual(by_id['fast']['duration_min'],by_id['economy']['duration_min'])
        for plan in result['plans']:
            self.assertEqual(plan['cost_rub'],sum(v['cost_rub'] for v in plan['vehicles']))
            for v in plan['vehicles']:
                self.assertAlmostEqual(sum(p['minutes'] for p in v['phases']),v['elapsed_time_min'],delta=.51)
                self.assertAlmostEqual(sum(v['cost_components'].values()),v['cost_rub'],delta=.51)
                uav=next(u for u in UAVS if u['id']==v['uav_id'])
                self.assertLessEqual(v['max_sortie_min'],uav['endurance_min']*(1-v['reserve_percent']/100)+1e-8)

    def test_incompatible_multi_result_rejected(self):
        with self.assertRaisesRegex(ValueError,'Несовместимые'):
            optimize(MissionRequest(area=AREA,result_types=['orthophoto','thermal_map']))

    def test_field_launch_point_replaces_remote_fleet_base(self):
        result = optimize(MissionRequest(
            area=AREA, payload_id='payload-sony-61',
            launch_point=(37.468, 55.782),
        ))
        for plan in result['plans']:
            for vehicle in plan['vehicles']:
                self.assertEqual(vehicle['base_id'], 'planned-field-site')
                self.assertIn('не обследован', vehicle['base_name'])

    def test_catalog_launch_site_name_is_kept_in_complete_plan(self):
        result = optimize(MissionRequest(
            area=AREA, payload_id='payload-sony-61',
            launch_point=(37.305, 55.878), launch_site={
                'id':'base-north','name':'ВПП Север','lon':37.305,'lat':55.878,
                'kind':'runway','runway_length_m':460,'heading_deg':82,
                'supports':['fixed_wing','multirotor','vtol'],'status':'open',
            },
        ))
        for plan in result['plans']:
            for vehicle in plan['vehicles']:
                self.assertEqual(vehicle['base_id'], 'base-north')
                self.assertEqual(vehicle['base_name'], 'ВПП Север')
                coordinates = vehicle['route']['coordinates']
                self.assertAlmostEqual(coordinates[0][0], 37.305, places=5)
                self.assertAlmostEqual(coordinates[0][1], 55.878, places=5)
                self.assertAlmostEqual(coordinates[-1][0], 37.305, places=5)
                self.assertAlmostEqual(coordinates[-1][1], 55.878, places=5)

    def test_actual_sortie_duration_keeps_declared_reserve(self):
        result = optimize(MissionRequest(
            area=AREA, payload_id='payload-sony-61',
            launch_point=(37.468, 55.782),
        ))
        for plan in result['plans']:
            for vehicle in plan['vehicles']:
                uav = next(item for item in UAVS if item['id'] == vehicle['uav_id'])
                allowed = uav['endurance_min'] * (1 - vehicle['reserve_percent'] / 100)
                self.assertLessEqual(vehicle['max_sortie_min'], allowed + 1e-8)

    def test_telemetry_real_time_distance(self):
        for site in SITES:
            points=track(site)
            for second in range(0,1200,17):
                speed=site[6]/3.6
                a,*_=sample(points,second*speed)
                b,*_=sample(points,(second+1)*speed)
                self.assertLessEqual(math.dist(a,b),speed+.01)
                self.assertGreater(math.dist(a,b),speed*.95)

    def test_simulation_coherent_and_shared(self):
        a,b=snapshot(100),snapshot(100)
        self.assertEqual(a,b)
        self.assertEqual(len(a['vehicles']),10)
        self.assertEqual(len(a['areas']['features']),10)
        self.assertTrue(all(v['simulation_rate']==1 for v in a['vehicles']))
        routes={}
        for feature in a['routes']['features']:
            routes.setdefault(feature['properties']['color'],[]).append(feature['geometry']['coordinates'])
        for vehicle in a['vehicles']:
            route_parts=routes[vehicle['color']]
            # The simulator and displayed route share the same coordinate
            # generator; the current aircraft point must lie on a route segment.
            point=(vehicle['lon'],vehicle['lat'])
            distance=min(self._point_segment_distance(point,tuple(start),tuple(end)) for route in route_parts for start,end in zip(route,route[1:]))
            self.assertLess(distance,1e-9)

    def test_each_sortie_has_five_minute_ground_service(self):
        site = SITES[0]
        distances, times, total = _motion_timeline(site)
        offset_distance = site.phase_offset * total
        index = min(len(distances) - 2, max(0, bisect_right(distances, offset_distance) - 1))
        ratio = (offset_distance - distances[index]) / (distances[index + 1] - distances[index])
        offset_time = times[index] + (times[index + 1] - times[index]) * ratio
        service_start = times[-1] - offset_time

        landed_distance, servicing, service_elapsed = _distance_at_time(site, service_start + 1)
        self.assertTrue(servicing)
        self.assertAlmostEqual(landed_distance, total, delta=.01)
        self.assertAlmostEqual(service_elapsed, 1, delta=.01)

        _, still_servicing, _ = _distance_at_time(site, service_start + GROUND_SERVICE_SECONDS - 1)
        restarted_distance, restarted, _ = _distance_at_time(site, service_start + GROUND_SERVICE_SECONDS)
        self.assertTrue(still_servicing)
        self.assertFalse(restarted)
        self.assertAlmostEqual(restarted_distance, 0, delta=.01)

    def test_fixed_wing_return_has_no_sharp_course_changes(self):
        for site in (item for item in SITES if item.uav_type == 'fixed_wing'):
            outbound, _, inbound = route_parts(site)
            for leg in (outbound, inbound):
                headings = [
                    math.degrees(math.atan2(end[1]-start[1], end[0]-start[0]))
                    for start, end in zip(leg, leg[1:])
                    if math.dist(start, end) > .01
                ]
                turns = [abs((following-current+180) % 360 - 180) for current, following in zip(headings, headings[1:])]
                self.assertLess(max(turns), 22, site.uav_id)

    def test_transit_routes_are_close_to_the_shortest_distance(self):
        for site in SITES:
            outbound, _, inbound = route_parts(site)
            for leg in (outbound, inbound):
                flown = sum(math.dist(start, end) for start, end in zip(leg, leg[1:]))
                direct = math.dist(leg[0], leg[-1])
                self.assertLess(flown / direct, 1.08, site.uav_id)

    def test_survey_entry_and_exit_are_smooth(self):
        for site in SITES:
            outbound, survey, inbound = route_parts(site)
            joined = (outbound[-2:], survey[:2], survey[-2:], inbound[:2])
            headings = [
                math.degrees(math.atan2(points[-1][1]-points[0][1], points[-1][0]-points[0][0]))
                for points in joined
            ]
            entry_turn = abs((headings[1]-headings[0]+180) % 360 - 180)
            exit_turn = abs((headings[3]-headings[2]+180) % 360 - 180)
            self.assertLess(entry_turn, 22, site.uav_id)
            self.assertLess(exit_turn, 22, site.uav_id)

    def test_simulated_displacement_matches_reported_phase_speed(self):
        first = snapshot(100)
        second = snapshot(101, simulation_rate=4)
        self.assertEqual(second['simulation_rate'], 4)
        by_id = {vehicle['uav_id']: vehicle for vehicle in second['vehicles']}
        for vehicle in first['vehicles']:
            following = by_id[vehicle['uav_id']]
            mean_lat = math.radians((vehicle['lat'] + following['lat']) / 2)
            dx = (following['lon'] - vehicle['lon']) * 111320 * math.cos(mean_lat)
            dy = (following['lat'] - vehicle['lat']) * 110540
            displacement = math.hypot(dx, dy)
            expected = (vehicle['speed_kmh'] + following['speed_kmh']) / 2 / 3.6
            self.assertAlmostEqual(displacement, expected, delta=max(.35, expected * .08))

    def test_next_base_operations_follow_the_flight_cycle(self):
        packet = snapshot(100)
        self.assertEqual(len(packet['vehicles']), len(SITES))
        for vehicle in packet['vehicles']:
            takeoff = vehicle['next_takeoff_seconds']
            landing = vehicle['next_landing_seconds']
            self.assertGreaterEqual(takeoff, 0)
            self.assertGreaterEqual(landing, 0)
            if vehicle['status'] == 'servicing':
                self.assertLessEqual(takeoff, GROUND_SERVICE_SECONDS)
                self.assertGreater(landing, takeoff)
            else:
                self.assertLess(landing, takeoff)

    @staticmethod
    def _point_segment_distance(point, start, end):
        dx,dy=end[0]-start[0],end[1]-start[1]
        length=dx*dx+dy*dy
        if not length:return math.dist(point,start)
        ratio=max(0,min(1,((point[0]-start[0])*dx+(point[1]-start[1])*dy)/length))
        projection=(start[0]+ratio*dx,start[1]+ratio*dy)
        return math.dist(point,projection)

import unittest
from datetime import datetime, timedelta, timezone

from app.models import MissionRequest
from app.optimizer import MAX_OPERATION_WINDOW_MIN, MAX_SORTIES_PER_UAV, optimize


LOCAL_AREA = {'type': 'Polygon', 'coordinates': [[
    [37.245, 55.965], [37.254, 55.965], [37.254, 55.960],
    [37.245, 55.960], [37.245, 55.965],
]]}
LARGE_AREA = {'type': 'Polygon', 'coordinates': [[
    [37.21, 55.97], [37.28, 55.97], [37.29, 55.93],
    [37.21, 55.93], [37.21, 55.97],
]]}
CAMPAIGN_AREA = {'type': 'Polygon', 'coordinates': [[
    [37.24, 55.97], [37.28, 55.97], [37.28, 55.95],
    [37.24, 55.95], [37.24, 55.97],
]]}
CORRIDOR = {'type': 'LineString', 'coordinates': [
    [37.246, 55.965], [37.250, 55.963], [37.253, 55.960],
]}
SITE = {
    'id': 'base-demo-ligachevo', 'name': 'Учебная ВПП Лигачёво',
    'lon': 37.25, 'lat': 55.975, 'kind': 'runway',
    'runway_length_m': 460, 'heading_deg': 180,
    'supports': ['fixed_wing', 'multirotor', 'vtol'], 'status': 'open',
}


class ProductMatrixTest(unittest.TestCase):
    def test_each_technology_produces_a_bounded_plan_on_small_geometry(self):
        products = [
            ('orthophoto', 'rgb', 'payload-sony-61', .70, .60),
            ('thermal_map', 'ir', 'payload-thermal-814', .75, .65),
            ('point_cloud', 'lidar', 'payload-agm-lidar', .65, .50),
            ('magnetic_map', 'geophysical', 'payload-quantum-mag', .50, .20),
            ('powerline_report', 'rgb', 'payload-sony-61', .75, .65),
            ('digital_twin', 'rgb', 'payload-sony-61', .80, .70),
            ('ndvi', 'multispectral', 'payload-pollux', .75, .65),
        ]
        for result_type, survey_type, payload_id, forward_overlap, side_overlap in products:
            with self.subTest(result_type=result_type):
                result = optimize(MissionRequest(
                    area=CORRIDOR if result_type == 'powerline_report' else LOCAL_AREA,
                    result_type=result_type, survey_type=survey_type,
                    payload_id=payload_id, launch_point=(SITE['lon'], SITE['lat']),
                    launch_site=SITE, max_flight_altitude_m=150 if result_type == 'ndvi' else 100,
                    survey_line_spacing_m=40,
                    gsd_cm_px=8 if survey_type in {'ir', 'multispectral'} else 5,
                    forward_overlap=forward_overlap, side_overlap=side_overlap,
                ))
                self.assertTrue(result['plans'])
                for plan in result['plans']:
                    self.assertLessEqual(plan['duration_min'], MAX_OPERATION_WINDOW_MIN)
                    self.assertGreater(plan['cost_rub'], 0)
                    for vehicle in plan['vehicles']:
                        self.assertLessEqual(vehicle['sorties'], MAX_SORTIES_PER_UAV)
                        self.assertLessEqual(vehicle['max_sortie_min'], vehicle['usable_endurance_min'])
                        self.assertEqual(vehicle['route']['type'], 'LineString')
                        self.assertGreater(vehicle['strip_count'], 0)
                        self.assertEqual(vehicle['turn_radius_m'] > 0, vehicle['uav_type'] == 'fixed_wing')
                        self.assertAlmostEqual(vehicle['route']['coordinates'][0][0], SITE['lon'], places=5)
                        self.assertAlmostEqual(vehicle['route']['coordinates'][-1][1], SITE['lat'], places=5)

    def test_large_lidar_area_reports_unreachable_explicit_deadline(self):
        start = datetime(2026, 9, 25, 9, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, 'Срок невыполним|Заданный срок невыполним'):
            optimize(MissionRequest(
                area=LARGE_AREA, result_type='point_cloud', survey_type='lidar',
                payload_id='payload-agm-lidar', launch_point=(SITE['lon'], SITE['lat']),
                launch_site=SITE, max_flight_altitude_m=100,
                survey_line_spacing_m=40,
                earliest_start=start, deadline=start + timedelta(hours=12),
            ))

    def test_lidar_campaign_calculates_beyond_old_six_sortie_cap(self):
        result = optimize(MissionRequest(
            area=CAMPAIGN_AREA, result_type='point_cloud', survey_type='lidar',
            payload_id='payload-agm-lidar', launch_point=(SITE['lon'], SITE['lat']),
            launch_site=SITE, max_flight_altitude_m=100,
            survey_line_spacing_m=40,
        ))
        self.assertGreater(result['plans'][0]['sorties'], 6)
        self.assertGreater(result['plans'][0]['duration_min'], 720)

    def test_result_type_cannot_claim_another_sensor(self):
        with self.assertRaisesRegex(ValueError, 'Несовместимые результаты'):
            optimize(MissionRequest(area=LOCAL_AREA, result_type='point_cloud', survey_type='rgb'))

    def test_fleet_cap_and_deadline_are_hard_constraints(self):
        start = datetime(2026, 9, 25, 9, tzinfo=timezone.utc)
        result = optimize(MissionRequest(
            area=LOCAL_AREA, launch_site=SITE, launch_point=(SITE['lon'], SITE['lat']),
            payload_id='payload-sony-61', max_uavs=1,
            earliest_start=start, deadline=start + timedelta(hours=10),
        ))
        self.assertTrue(result['plans'])
        self.assertTrue(all(plan['uav_count'] == 1 and plan['duration_min'] <= 600 for plan in result['plans']))
        with self.assertRaisesRegex(ValueError, 'Срок невыполним|Заданный срок невыполним'):
            optimize(MissionRequest(
                area=LOCAL_AREA, launch_site=SITE, launch_point=(SITE['lon'], SITE['lat']),
                payload_id='payload-sony-61', max_uavs=1,
                earliest_start=start, deadline=start + timedelta(minutes=2),
            ))


if __name__ == '__main__':
    unittest.main()

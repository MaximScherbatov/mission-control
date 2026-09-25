import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import main


class OrderScheduleApiTests(unittest.TestCase):
    def test_optimizer_resolves_runway_from_catalog_not_client_coordinates(self):
        main.DATABASE_URL = ""
        area = {"type":"Polygon","coordinates":[[
            [37.247,55.970],[37.253,55.970],[37.253,55.967],
            [37.247,55.967],[37.247,55.970],
        ]]}
        with TestClient(main.app) as client:
            client.post("/api/auth/login", json={"username":"dispatcher","password":"dispatcher"})
            missing = client.post("/api/missions/optimize", json={
                "area":area,"launch_site_id":"missing-runway",
                "launch_point":[37.25,55.975],"payload_id":"payload-sony-61",
            })
            self.assertEqual(missing.status_code, 422)
            response = client.post("/api/missions/optimize", json={
                "area":area,"launch_site_id":"base-demo-ligachevo",
                "launch_point":[20.0,20.0],"payload_id":"payload-sony-61",
            })
            self.assertEqual(response.status_code, 200, response.text)
            vehicles = [vehicle for plan in response.json()['plans'] for vehicle in plan['vehicles']]
            self.assertTrue(vehicles)
            self.assertTrue(all(vehicle['base_id'] == 'base-demo-ligachevo' for vehicle in vehicles))
            fixed = [vehicle for vehicle in vehicles if vehicle['uav_type'] == 'fixed_wing']
            self.assertTrue(all(vehicle['runway_heading_deg'] == 180 for vehicle in fixed))

            calculated = response.json()
            self.assertIn('route_planning', calculated['timings_ms'])
            self.assertLess(calculated['timings_ms']['settlement_lookup'], 5000,
                            'Overpass must not block interactive calculation')
            self.assertIn(calculated['recommended_plan_id'], calculated['settlement_assessments'])
            self.assertIn(calculated['settlement_assessments'][calculated['recommended_plan_id']]['status'],
                          {'COVERED', 'PARTIALLY_COVERED', 'NOT_COVERED'})
            mission_id = calculated['mission_id']
            plan_id = calculated['recommended_plan_id']
            first_vehicle = next(vehicle for plan in calculated['plans'] if plan['id'] == plan_id for vehicle in plan['vehicles'])
            scheduled = client.post('/api/schedule/batch', json={'entries': [{
                'date': str(date.today() + timedelta(days=6)), 'time': '09:00',
                'title': 'Проверка экспорта из календаря', 'location': 'Лигачёво',
                'uavId': first_vehicle['uav_id'], 'uavName': first_vehicle['uav_name'],
                'duration': first_vehicle['elapsed_time_min'] / 60,
                'product': 'Ортофото', 'source': 'planner',
                'simulation': {'missionId': mission_id, 'planId': plan_id, 'route': first_vehicle['route']},
            }]})
            self.assertEqual(scheduled.status_code, 201, scheduled.text)
            entry = scheduled.json()['items'][0]
            export = client.get(
                f"/api/missions/{entry['simulation']['missionId']}/export",
                params={'plan': entry['simulation']['planId'], 'format': 'kml'},
            )
            self.assertEqual(export.status_code, 200, export.text)
            self.assertIn('<kml', export.text)

    def test_fleet_plan_is_scheduled_as_one_atomic_batch(self):
        main.DATABASE_URL = ""
        with TestClient(main.app) as client:
            client.post("/api/auth/login", json={"username": "dispatcher", "password": "dispatcher"})
            common = {
                "date": str(date.today() + timedelta(days=5)), "time": "09:00",
                "title": "Ортофотоплан · сектор", "location": "Учебный полигон",
                "duration": 2, "product": "Ортофото", "source": "planner",
                "missionGroupId": "fleet-demo-test",
            }
            first = {**common, "uavId": "uav-geoscan-201-01", "uavName": "Геоскан 201 · 01"}
            second = {**common, "uavId": "uav-geoscan-201-02", "uavName": "Геоскан 201 · 02"}
            created = client.post("/api/schedule/batch", json={"entries": [first, second]})
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["count"], 2)
            self.assertEqual({item["missionGroupId"] for item in created.json()["items"]}, {"fleet-demo-test"})

            before = client.get("/api/schedule").json()["count"]
            third = {**common, "uavId": "uav-geoscan-701-01", "uavName": "Геоскан 701 · 01"}
            rejected = client.post("/api/schedule/batch", json={"entries": [first, third]})
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(client.get("/api/schedule").json()["count"], before)

    def test_jsonb_text_values_are_normalized_for_database_views(self):
        self.assertEqual(main._json_object('{"title":"Тест","area_km2":1.5}'), {"title": "Тест", "area_km2": 1.5})
        original = {"title": "Тест"}
        self.assertIs(main._json_object(original), original)

    def test_approved_order_is_scheduled_atomically(self):
        main.DATABASE_URL = ""
        with TestClient(main.app) as client:
            login = client.post("/api/auth/login", json={"username": "dispatcher", "password": "dispatcher"})
            self.assertEqual(login.status_code, 200)

            payload = {
                "date": str(date.today() + timedelta(days=4)),
                "time": "08:00",
                "title": "NDVI-мониторинг опытных полей",
                "location": "Красногорский район",
                "uavId": "geoscan-401-geo",
                "uavName": "Геоскан 401 · Геодезия",
                "duration": 2.5,
                "product": "NDVI",
                "source": "planner",
                "orderId": "10000000-0000-4000-8000-000000000004",
                "orderNumber": "MC-26079",
                "customerName": "АО «АгроТех»",
            }
            created = client.post("/api/schedule", json=payload)
            self.assertEqual(created.status_code, 201, created.text)

            orders = client.get("/api/orders").json()["items"]
            linked = next(item for item in orders if item["id"] == payload["orderId"])
            self.assertEqual(linked["status"], "scheduled")

            rejected = client.post("/api/schedule", json={
                **payload,
                "date": str(date.today() + timedelta(days=7)),
                "time": "12:00",
                "orderId": "10000000-0000-4000-8000-000000000001",
                "orderNumber": "MC-26091",
            })
            self.assertEqual(rejected.status_code, 409)
            self.assertIn("согласованный заказ", rejected.json()["detail"])

    def test_demo_flight_transitions_and_keeps_replay_route(self):
        main.DATABASE_URL = ""
        with TestClient(main.app) as client:
            client.post("/api/auth/login", json={"username": "dispatcher", "password": "dispatcher"})
            payload = {
                "date": str(date.today()), "time": "23:55", "title": "Проверка реплея",
                "location": "Тестовый полигон", "uavId": "uav-geoscan-201-01",
                "uavName": "Геоскан 201 · 01", "duration": 1.2, "product": "Ортофото",
                "source": "planner", "simulation": {
                    "missionId": "mission-test", "planId": "safe", "uavType": "fixed_wing",
                    "altitudeM": 120, "color": "#f15a32", "baseName": "Площадка",
                    "durationSeconds": 150, "actualDurationMin": 72,
                    "route": {"type": "LineString", "coordinates": [[37.0, 55.0], [37.1, 55.1], [37.0, 55.0]]},
                },
            }
            created = client.post("/api/schedule", json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            entry_id = created.json()["id"]
            active = client.patch(f"/api/schedule/{entry_id}", json={"status": "active", "demoStartedAt": "2026-09-22T12:00:00+03:00"})
            self.assertEqual(active.status_code, 200, active.text)
            self.assertEqual(active.json()["simulation"]["route"]["type"], "LineString")
            completed = client.patch(f"/api/schedule/{entry_id}", json={"status": "completed", "demoCompletedAt": "2026-09-22T12:02:30+03:00"})
            self.assertEqual(completed.status_code, 200, completed.text)
            rejected = client.patch(f"/api/schedule/{entry_id}", json={"status": "active"})
            self.assertEqual(rejected.status_code, 409)

    def test_maintenance_blocks_flights_across_midnight(self):
        main.DATABASE_URL = ""
        with TestClient(main.app) as client:
            client.post("/api/auth/login", json={"username": "dispatcher", "password": "dispatcher"})
            date1 = date.today() + timedelta(days=10)
            base = {
                "date": str(date1), "time": "23:00", "title": "Плановое ТО",
                "location": "Сервисная зона", "uavId": "uav-geoscan-201-01",
                "uavName": "Геоскан 201 · 01", "duration": 3,
                "product": "Техническое обслуживание", "status": "maintenance", "source": "planner",
            }
            created = client.post("/api/schedule", json=base)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["status"], "maintenance")
            flight = {**base, "date": str(date1 + timedelta(days=1)), "time": "00:30",
                      "title": "Съёмка", "status": "planned", "product": "Ортофото"}
            rejected = client.post("/api/schedule", json=flight)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            flight["uavId"] = "uav-geoscan-201-02"
            flight["uavName"] = "Геоскан 201 · 02"
            accepted = client.post("/api/schedule", json=flight)
            self.assertEqual(accepted.status_code, 201, accepted.text)


if __name__ == "__main__":
    unittest.main()

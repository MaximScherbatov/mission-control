import unittest
from datetime import date, timedelta

from app.dadata import DadataService
from app.models import OrderCreate, ScheduleEntryCreate
from app.orders import ensure_transition
from app.simulation import snapshot
from app.weather import weather_risk


CUSTOMER = {"name": "ООО Тест", "inn": "7707083893"}
CONTACT = {"name": "Иван Петров", "phone": "+7 495 000-00-00", "email": "test@example.ru"}
POLYGON = {"type": "Polygon", "coordinates": [[[37.4, 55.7], [37.5, 55.7], [37.5, 55.8], [37.4, 55.7]]]}


class BusinessFlowTests(unittest.TestCase):
    def test_business_transition_guards(self):
        ensure_transition("new", "qualification")
        ensure_transition("approved", "scheduled")
        with self.assertRaises(ValueError):
            ensure_transition("new", "scheduled")

    def test_order_rejects_past_date(self):
        with self.assertRaises(ValueError):
            OrderCreate(title="Ортофотоплан участка", result_type="orthophoto", desired_date=str(date.today()-timedelta(days=1)), desired_time="09:00", location="Москва", area_km2=1, geometry=POLYGON, customer=CUSTOMER, contact=CONTACT)

    def test_only_corridor_products_accept_line(self):
        line = {"type": "LineString", "coordinates": [[37.4, 55.7], [37.5, 55.8]]}
        with self.assertRaises(ValueError):
            OrderCreate(title="Обычная площадная съёмка", result_type="orthophoto", desired_date=str(date.today()+timedelta(days=1)), desired_time="09:00", location="Москва", area_km2=1, geometry=line, customer=CUSTOMER, contact=CONTACT)
        valid = OrderCreate(title="Тепловизионная съёмка трассы", result_type="thermal_map", desired_date=str(date.today()+timedelta(days=1)), desired_time="06:00", location="Москва", area_km2=1, geometry=line, customer=CUSTOMER, contact=CONTACT)
        self.assertEqual(valid.geometry["type"], "LineString")

    def test_schedule_rejects_past(self):
        with self.assertRaises(ValueError):
            ScheduleEntryCreate(date=str(date.today()-timedelta(days=1)), time="09:00", title="Задание", location="Москва", uavId="uav-1", uavName="Борт 1", duration=2, product="Ортофото")

    def test_dadata_response_is_normalized(self):
        item = DadataService.normalize({"value":"ООО Тест","data":{"inn":"7707083893","kpp":"770701001","name":{"short_with_opf":"ООО Тест"},"address":{"value":"Москва"},"management":{"name":"Иванов И.И."},"state":{"status":"ACTIVE"}}})
        self.assertEqual(item["inn"], "7707083893")
        self.assertEqual(item["legal_address"], "Москва")

    def test_weather_risk_reacts_to_operational_limits(self):
        good = weather_risk({"wind_speed_mps": 2, "precipitation_mm": 0, "visibility_m": 20000, "cloud_cover_percent": 20})
        bad = weather_risk({"wind_speed_mps": 14, "precipitation_mm": 2, "visibility_m": 3000, "cloud_cover_percent": 100})
        self.assertGreater(good["score"], bad["score"])
        self.assertEqual(bad["level"], "critical")

    def test_attitude_telemetry_is_not_decorative(self):
        packets = [snapshot(second)["vehicles"] for second in (3, 9, 25, 70, 140)]
        self.assertTrue(any(abs(vehicle["pitch_deg"]) > .2 for packet in packets for vehicle in packet))
        self.assertTrue(any(abs(vehicle["vertical_speed_mps"]) > .2 for packet in packets for vehicle in packet))


if __name__ == "__main__":
    unittest.main()

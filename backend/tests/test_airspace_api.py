import unittest

from fastapi.testclient import TestClient

from app import main


class AirspaceApiTests(unittest.TestCase):
    def test_summary_and_zones_are_available_after_login(self):
        main.DATABASE_URL = ""
        with TestClient(main.app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": "dispatcher", "password": "dispatcher"},
            )
            self.assertEqual(login.status_code, 200, login.text)

            response = client.get("/api/airspace/summary")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["counts"]["obstacle"], 6)

            zones = client.get("/api/airspace/zones?category=obstacle")
            self.assertEqual(zones.status_code, 200, zones.text)
            self.assertEqual(zones.json()["count"], 6)


if __name__ == "__main__":
    unittest.main()

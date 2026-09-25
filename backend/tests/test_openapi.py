import unittest

from app.main import app


class OpenApiTests(unittest.TestCase):
    def test_flight_plan_endpoints_are_documented(self):
        schema = app.openapi()
        self.assertIn("/api/missions/{mission_id}", schema["paths"])
        self.assertIn("/api/missions/{mission_id}/flight-plan", schema["paths"])
        self.assertIn("/api/missions/{mission_id}/export", schema["paths"])

    def test_every_http_operation_has_a_business_tag(self):
        schema = app.openapi()
        operations = [
            operation
            for methods in schema["paths"].values()
            for method, operation in methods.items()
            if method in {"get", "post", "put", "patch", "delete"}
        ]
        self.assertTrue(operations)
        self.assertTrue(all(operation.get("tags") and operation["tags"] != ["default"] for operation in operations))


if __name__ == "__main__":
    unittest.main()

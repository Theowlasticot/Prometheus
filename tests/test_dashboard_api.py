import unittest
from fastapi.testclient import TestClient
from dashboard.app import app

class TestDashboardAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_stats_endpoint(self):
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("kpis", data)
        self.assertIn("config", data)

    def test_servers_endpoint(self):
        response = self.client.get("/api/servers")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("servers", data)
        self.assertIn("current", data)

    def test_config_endpoint(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        if "credentials" in data and "password" in data["credentials"]:
            # Ensure password is redacted
            self.assertNotEqual(data["credentials"]["password"], "secret_raw")

if __name__ == "__main__":
    unittest.main()

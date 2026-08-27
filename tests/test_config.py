import os
import unittest
from data.config_settings import (
    get_username,
    get_password,
    get_headless,
    get_threads,
    get_mission_delay,
    get_transport_delay,
    get_server_code,
    get_server_url,
    is_alliance_mission_name,
)

class TestConfigSettings(unittest.TestCase):
    def test_get_username_and_password(self):
        # Should return string (empty or value)
        username = get_username()
        password = get_password()
        self.assertIsInstance(username, str)
        self.assertIsInstance(password, str)

    def test_get_headless(self):
        headless = get_headless()
        self.assertIsInstance(headless, bool)

    def test_get_threads_bounds(self):
        threads = get_threads()
        self.assertGreaterEqual(threads, 1)
        self.assertLessEqual(threads, 8)

    def test_get_mission_and_transport_delays(self):
        m_delay = get_mission_delay()
        t_delay = get_transport_delay()
        self.assertGreaterEqual(m_delay, 3)
        self.assertGreaterEqual(t_delay, 5)

    def test_get_server_url(self):
        url = get_server_url()
        self.assertTrue(url.startswith("http"))
        self.assertTrue(url.endswith("/"))

    def test_is_alliance_mission_name(self):
        self.assertTrue(is_alliance_mission_name("[Alliance] Large Fire"))
        self.assertTrue(is_alliance_mission_name("[verband] Grossbrand"))
        self.assertFalse(is_alliance_mission_name("Structure Fire"))

if __name__ == "__main__":
    unittest.main()

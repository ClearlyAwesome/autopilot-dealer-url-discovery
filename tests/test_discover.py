import datetime as dt
import unittest
from discover import url_clean, same_site, score, links, eligible

class TestDiscovery(unittest.TestCase):
    def test_url_safety(self):
        self.assertEqual(url_clean("example.com"), "https://example.com/")
        self.assertEqual(url_clean("http://example.com"), "http://example.com/")
        self.assertFalse(url_clean("http://127.0.0.1"))
        self.assertFalse(url_clean("http://localhost"))
        self.assertFalse(url_clean("file:///etc/passwd"))

    def test_domains(self):
        self.assertTrue(same_site("https://example.com", "https://inventory.example.com"))
        self.assertFalse(same_site("https://example.com", "https://example.com.evil.org"))

    def test_categories(self):
        self.assertGreaterEqual(score("https://dealer.com/new-vehicles/", "New Vehicles", "new"), 5)
        self.assertEqual(score("https://dealer.com/new-vehicles/", "New Vehicles", "used"), 0)
        self.assertGreaterEqual(score("https://dealer.com/inventory/used", "Used Cars", "used"), 5)
        self.assertEqual(score("https://dealer.com/used-vehicle-specials", "Used Specials", "used"), 0)
        self.assertEqual(score("https://dealer.com/certified-only", "Certified", "used"), 0)
        self.assertGreaterEqual(score("https://dealer.com/llm/inventory/?type=new", "", "new"), 5)

    def test_links(self):
        html = '<a href="/new-vehicles/">New Inventory</a><a href="/used-vehicles/">Used Inventory</a><a href="https://other.com/used-vehicles/">Used</a>'
        self.assertEqual(len(links(html, "https://dealer.com/")), 2)

    def test_checkpoint(self):
        now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
        row = {"New Inventory URL": "", "Used Inventory URL": ""}
        self.assertTrue(eligible(2, row, {}, now))
        self.assertFalse(eligible(2, {"New Inventory URL": "a", "Used Inventory URL": "b"}, {}, now))
        self.assertFalse(eligible(2, row, {2: [("2026-10-18T00:00:00+00:00", "homepage_error")]}, now))
        self.assertTrue(eligible(2, row, {2: [("2026-10-10T00:00:00+00:00", "homepage_error")]}, now))

if __name__ == "__main__":
    unittest.main()

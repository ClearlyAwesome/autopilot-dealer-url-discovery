import unittest
from unittest.mock import patch
from contact_discovery import clean_email, extract_contacts, inspect, link_candidates

DEALER = {"Sheet Row": "2", "Source ID": "101", "Dealership": "Example Toyota",
          "City": "Reno", "State": "NV", "Website": "https://www.exampletoyota.com/"}

class ContactDiscoveryTests(unittest.TestCase):
    def test_never_generates_address(self):
        self.assertEqual(extract_contacts('<h1>Reno Toyota</h1><p>Contact sales</p>', DEALER['Website'], DEALER), [])

    def test_mailto_is_sourced_not_an_adf_endpoint(self):
        html = '<section><h3>Jane Smith — Internet Sales Director</h3><a href="mailto:jane@exampletoyota.com">Email Jane</a></section>'
        rows = extract_contacts(html, DEALER['Website'] + 'staff', DEALER)
        self.assertEqual(rows[0]['Email'], 'jane@exampletoyota.com')
        self.assertEqual(rows[0]['Role Hint'], 'internet_sales')
        self.assertEqual(rows[0]['CRM Address Confirmed'], 'NO')
        self.assertEqual(rows[0]['ADF Auto Send Authorized'], 'NO')
        self.assertEqual(rows[0]['Evidence URL'], DEALER['Website'] + 'staff')

    def test_reject_obvious_fake_templates(self):
        self.assertEqual(clean_email('mailto:name@domain.com'), '')
        self.assertEqual(clean_email('not-an-email'), '')

    def test_same_site_links_only(self):
        html = '<a href="/staff">Staff</a><a href="https://unrelated.com/staff">Staff</a><a href="/service">Service</a>'
        self.assertEqual(link_candidates(html, DEALER['Website']), ['https://www.exampletoyota.com/staff'])

    @patch('contact_discovery.time.sleep')
    @patch('contact_discovery.fetch')
    def test_source_provenance_without_sending(self, mock_fetch, unused_sleep):
        home = '<h1>Example Toyota</h1><a href="/staff">Meet Our Staff</a>'
        staff = '<div><span>General Sales Manager</span><a href="mailto:manager@exampletoyota.com">manager@exampletoyota.com</a></div>'
        mock_fetch.side_effect = [(home, 'https://www.exampletoyota.com/'), (staff, 'https://www.exampletoyota.com/staff')]
        rows, audit = inspect(DEALER)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Evidence URL'], 'https://www.exampletoyota.com/staff')
        self.assertEqual(audit['Status'], 'contacts_found')

if __name__ == '__main__':
    unittest.main()

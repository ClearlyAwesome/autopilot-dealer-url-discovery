# Auto Pilot — Nationwide Inventory URL Discovery

This repository is **separate from Auto Pilot production**. It researches the existing [19,169-dealership Google Sheet](https://docs.google.com/spreadsheets/d/1tGBSFp-bxhW1_uocOU0uBinXtPos-TKPJLyNDmrNrX0/edit) in small batches using public dealership websites. It does not scrape vehicle inventory records, use Scrape.do, or charge an API.

## How it works

GitHub Actions runs a batch automatically every six hours (100 dealerships per run by default). The Python worker reads the live directory, skips rows with both URLs, visits official websites, checks robots.txt, inspects navigation and sitemaps, and verifies candidate new/used inventory pages. It writes new discoveries only to the empty cells in columns D and E. It appends a per-dealer result to **Discovery Audit** and updates **Discovery Progress** in the same Sheet. Unresolved/blocked sites remain blank; URLs are never invented.

## Required one-time credential setup

1. In Google Cloud Console, create or select a project. Enable **Google Sheets API** and create a dedicated **service account**. Generate a JSON key for this service account.
2. Open the [target Google Sheet](https://docs.google.com/spreadsheets/d/1tGBSFp-bxhW1_uocOU0uBinXtPos-TKPJLyNDmrNrX0/edit) → Share → add the service account's `client_email` as **Editor**.
3. In this GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**. Name it `GOOGLE_SERVICE_ACCOUNT_JSON` and paste the full JSON key. **Never put this key in code, issues, chats, or the Sheet.**
4. Go to **Actions → Auto Pilot inventory URL discovery → Run workflow**, choose batch size **5** for the first smoke test, and check the run log plus the Sheet's **Discovery Audit** and **Discovery Progress** tabs. Subsequent runs are scheduled automatically.

The ChatGPT Google Drive connection cannot authenticate an unattended GitHub runner; the separate service-account authorization is essential.

## Limits and safeguards

- No paid scraping provider. GitHub Actions has free usage allowances, not unlimited resources. Check the GitHub billing page; set a $0 spending cap if available.
- A populated URL means the landing page was reachable and classified when checked, **not** that its vehicle inventory is accurate or that it can be scraped.
- The original directory is historical. Closed, renamed, merged, or redirected dealerships may remain unresolved. Do not treat guessed URL paths as verified.
- Respect robots.txt and website rate limits. Do not bypass access controls or anti-bot systems.
- Scheduled GitHub Actions runs can be delayed or disabled after inactivity. Watch the audit tab and run history.
- This repository currently may be public; code contains **no credentials**. If you prefer privacy, change visibility in GitHub Settings → General → Danger Zone.
- Do not modify production Auto Pilot code as part of this worker.

## Local tests

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py" -v
```

To run locally, set `GOOGLE_SERVICE_ACCOUNT_JSON` and execute `python src/discover.py --batch-size 5 --workers 2`. Do not commit secrets.

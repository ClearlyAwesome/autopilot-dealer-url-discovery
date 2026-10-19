# Auto Pilot — automated dealership inventory URL research

**No Google Cloud setup. No API key. No paid scraping credits.**

This repository is separate from the Auto Pilot production app. It contains a snapshot of all **19,169 dealerships** from the [Auto Pilot dealership Google Sheet](https://docs.google.com/spreadsheets/d/1tGBSFp-bxhW1_uocOU0uBinXtPos-TKPJLyNDmrNrX0/edit), including existing new and used inventory links.

## Automatic operation

The [GitHub Actions workflow](../../actions/workflows/discover.yml) is scheduled **every six hours**, checking up to **100 dealerships per run**. It visits dealer homepages, follows official inventory navigation and sitemaps, respects robots.txt, and only saves new/used category pages it can reach and classify. It does not scrape vehicle listings, invent URLs, or modify production Auto Pilot.

**Results are saved automatically back into this repository** (not the Google Sheet):

- [discovered inventory URLs](results/discoveries.csv) — created on the first successful run
- [full attempt log](results/audit.csv) — created on the first successful run
- [readable progress report](results/PROGRESS.md) — created on the first successful run
- [machine-readable progress](results/progress.json) — created on the first successful run

The original dealer snapshot is split into 20 files under [data/](data/), preserving the original Google Sheet row numbers for future synchronization. GitHub Actions commits the results using its built-in repository token. No user credentials are needed.

## Start and check

Open [Actions → Auto Pilot inventory URL discovery](../../actions/workflows/discover.yml), click **Run workflow**, and choose batch size **5** for the initial test. After a successful run, the six-hour schedule proceeds automatically.

If GitHub Actions reports a permissions error on the result commit, change **Settings → Actions → General → Workflow permissions** to **Read and write permissions** and rerun. GitHub account/repository policy can override workflow permissions.

## Practical limitations

- The directory is historical and contains closed/renamed dealers and stale website addresses; some records cannot be verified without external research.
- Results are stored in GitHub. **Automatic Google Sheets syncing is not enabled**; adding that later requires Google authorization, or a separate periodic import.
- GitHub Actions scheduling is best-effort and can be delayed or paused. GitHub-hosted runner usage is subject to GitHub's free allowances.
- The current repository visibility is public; dealership names and public website URLs are publicly readable.
- This crawler does not bypass anti-bot measures, logins, robots exclusions, or scrape vehicle listings.

## Developer checks

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src python src/repo_worker.py --batch-size 5
```

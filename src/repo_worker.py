#!/usr/bin/env python3
"""Credential-free GitHub Actions worker; writes research to this repository."""
import argparse
import csv
import datetime as dt
import glob
import json
import os
from pathlib import Path
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from discover import inspect, eligible, now_utc

FIELDS = ["Sheet Row", "Source ID", "Dealership", "Website", "Checked At UTC", "Status",
          "New Inventory URL", "Used Inventory URL", "New Evidence", "Used Evidence",
          "Final Homepage", "Notes"]
RESULT_FIELDS = ["Sheet Row", "Source ID", "Dealership", "Website", "New Inventory URL",
                 "Used Inventory URL", "New Evidence", "Used Evidence", "Checked At UTC", "Status"]


def read_csv(path):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, headers, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    sources = sorted(glob.glob("data/dealers-*.csv"))
    if not sources:
        raise RuntimeError("No data/dealers-*.csv source files found")
    rows = []
    for path in sources:
        for row in read_csv(path):
            try:
                row_num = int(row["Sheet Row"])
            except (ValueError, KeyError):
                continue
            if row.get("Dealership", "").strip():
                rows.append((row_num, row))
    if not rows:
        raise RuntimeError("Source dealership directory is empty")
    rows.sort()
    if len({r for r, _ in rows}) != len(rows):
        raise RuntimeError("Duplicate sheet row numbers in source files")

    audit = read_csv("results/audit.csv")
    found = read_csv("results/discoveries.csv")
    by_row = {int(x["Sheet Row"]): x for x in found if x.get("Sheet Row", "").isdigit()}
    history = defaultdict(list)
    for x in audit:
        if x.get("Sheet Row", "").isdigit():
            history[int(x["Sheet Row"])].append((x.get("Checked At UTC"), x.get("Status")))

    for number, row in rows:
        if number in by_row:
            old = by_row[number]
            for col in ("New Inventory URL", "Used Inventory URL"):
                if old.get(col):
                    row[col] = old[col]
    now = now_utc()
    todo = [(number, row) for number, row in rows if eligible(number, row, history, now)]
    todo.sort(key=lambda item: (item[0] in history, item[0]))
    selected = todo[:min(250, max(1, args.batch_size))]
    checked = []
    with ThreadPoolExecutor(max_workers=min(10, max(1, args.workers))) as pool:
        futures = [pool.submit(inspect, number, row) for number, row in selected]
        for future in as_completed(futures):
            result = future.result()
            checked.append(result)
            print(f"Row {result.number}: {result.status}", flush=True)
    count_new = count_used = 0
    for result in checked:
        number = result.number
        prior = by_row.get(number, {})
        row = result.row
        new = prior.get("New Inventory URL") or row.get("New Inventory URL") or result.new
        used = prior.get("Used Inventory URL") or row.get("Used Inventory URL") or result.used
        count_new += bool(result.new and not prior.get("New Inventory URL") and not row.get("New Inventory URL"))
        count_used += bool(result.used and not prior.get("Used Inventory URL") and not row.get("Used Inventory URL"))
        if result.new or result.used:
            by_row[number] = {
                "Sheet Row": str(number), "Source ID": row.get("Source ID", ""),
                "Dealership": row.get("Dealership", ""), "Website": row.get("Website", ""),
                "New Inventory URL": new, "Used Inventory URL": used,
                "New Evidence": result.new_evidence or prior.get("New Evidence", ""),
                "Used Evidence": result.used_evidence or prior.get("Used Evidence", ""),
                "Checked At UTC": result.checked, "Status": result.status
            }
    audit.extend(dict(zip(FIELDS, result.audit())) for result in sorted(checked, key=lambda x: x.number))
    write_csv("results/audit.csv", FIELDS, audit)
    write_csv("results/discoveries.csv", RESULT_FIELDS, sorted(by_row.values(), key=lambda x: int(x["Sheet Row"])))

    base_both = sum(bool(row.get("New Inventory URL")) and bool(row.get("Used Inventory URL")) for _, row in rows)
    base_new = sum(bool(row.get("New Inventory URL")) for _, row in rows)
    base_used = sum(bool(row.get("Used Inventory URL")) for _, row in rows)
    new_count = base_new + sum(bool(x.get("New Inventory URL")) and not dict(rows)[int(x["Sheet Row"])].get("New Inventory URL") for x in by_row.values())
    used_count = base_used + sum(bool(x.get("Used Inventory URL")) and not dict(rows)[int(x["Sheet Row"])].get("Used Inventory URL") for x in by_row.values())
    both_count = base_both + sum(
        bool(x.get("New Inventory URL") or dict(rows)[int(x["Sheet Row"])].get("New Inventory URL"))
        and bool(x.get("Used Inventory URL") or dict(rows)[int(x["Sheet Row"])].get("Used Inventory URL"))
        and not (dict(rows)[int(x["Sheet Row"])].get("New Inventory URL") and dict(rows)[int(x["Sheet Row"])].get("Used Inventory URL"))
        for x in by_row.values())
    summary = {
        "last_run_utc": now.isoformat(timespec="seconds"),
        "total_dealerships": len(rows),
        "checked_this_run": len(checked),
        "attempts_to_date": len(audit),
        "new_urls_added_this_run": count_new,
        "used_urls_added_this_run": count_used,
        "new_urls_total": new_count,
        "used_urls_total": used_count,
        "dealerships_with_both_urls": both_count,
        "remaining_to_research": len(todo) - len(selected),
        "status_counts": dict(Counter(r.status for r in checked)),
        "note": "URLs collected in GitHub results/discoveries.csv; Google Sheet not automatically synced."
    }
    Path("results").mkdir(exist_ok=True)
    Path("results/progress.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    Path("results/PROGRESS.md").write_text(
        "# Auto Pilot inventory URL discovery\n\n"
        f"Last run (UTC): **{summary['last_run_utc']}**\n\n"
        f"- Dealerships in directory: **{len(rows):,}**\n"
        f"- Dealerships with both URLs: **{both_count:,}**\n"
        f"- New inventory URLs: **{new_count:,}**\n"
        f"- Used inventory URLs: **{used_count:,}**\n"
        f"- Dealerships checked this run: **{len(checked)}**\n"
        f"- Total checks logged: **{len(audit):,}**\n"
        f"- New links found this run: **{count_new}**\n"
        f"- Used links found this run: **{count_used}**\n\n"
        "Results: [discovered URLs](discoveries.csv) · [audit](audit.csv)\n\n"
        "Results are stored in GitHub. Google Sheets synchronization is not enabled.\n",
        encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as f:
        f.write(Path("results/PROGRESS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

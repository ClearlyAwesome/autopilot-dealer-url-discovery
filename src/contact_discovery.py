#!/usr/bin/env python3
"""Find published dealership business contacts; never send leads or infer CRM endpoints.

Run: PYTHONPATH=src python src/contact_discovery.py --batch-size 20
Results are local only. Do NOT commit outputs to this public repository.
The existing discover.fetch layer applies robots.txt, bounded redirects, limits and timeouts.
"""
import argparse
import csv
import datetime as dt
import glob
import re
import time
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup
from discover import FetchError, RobotsBlocked, fetch, identity_ok, same_site, url_clean

CONTACT_PATH = re.compile(r"(?:meet[-_/ ](?:our[-_/ ])?team|staff|our[-_/ ]team|our[-_/ ]people|leadership|internet[-_/ ]sales|contact[-_/ ]us|contact)", re.I)
UNWANTED_PATH = re.compile(r"(?:careers|employment|jobs|service|parts|privacy|legal|accessibility|cookie|terms|press|media)", re.I)
EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}(?![\w.-])", re.I)
NON_CONTACT = {"example.com", "example.org", "domain.com", "email.com", "sentry.io", "wix.com", "wordpress.com", "dealer.com", "google.com", "facebook.com", "youtube.com"}
FIELDS = ["Sheet Row", "Source ID", "Dealership", "City", "State", "Website", "Email", "Context", "Role Hint", "Evidence URL", "Evidence Type", "Discovered At UTC", "CRM Address Confirmed", "ADF Auto Send Authorized"]
AUDIT_FIELDS = ["Sheet Row", "Dealership", "Website", "Checked At UTC", "Pages Checked", "Contacts Found", "Status", "Notes"]

def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def clean_email(raw):
    match = EMAIL.search(urllib.parse.unquote(raw or ""))
    if not match: return ""
    email = match.group().strip(".,;:()[]<>'\" ").lower()
    local, domain = email.rsplit("@", 1)
    if domain in NON_CONTACT or local in {"name", "test", "example", "yourname", "username"}: return ""
    return email

def role_hint(context):
    txt = context.lower()
    for pattern, label in ((r"internet (sales )?(director|manager)", "internet_sales"),
                           (r"bdc (director|manager|agent)", "bdc"),
                           (r"general sales manager", "gsm"),
                           (r"general manager", "gm"),
                           (r"dealer principal", "dealer_principal"),
                           (r"sales (director|manager|department|team)", "sales"),
                           (r"fleet (director|manager|sales)", "fleet")):
        if re.search(pattern, txt): return label
    return "other_public_contact"

def link_candidates(html, base, max_pages=4):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.select("a[href]"):
        href = url_clean(urllib.parse.urljoin(base, a["href"]))
        if not href or not same_site(base, href): continue
        label = a.get_text(" ", strip=True)[:100]
        path = urllib.parse.urlsplit(href).path
        if CONTACT_PATH.search(path + " " + label) and not UNWANTED_PATH.search(path + " " + label):
            score = 4 if re.search(r"staff|meet[-_/ ](?:our[-_/ ])?team|internet", path + " " + label, re.I) else 2
            found.append((score, href))
    return [u for _, u in sorted(set(found), reverse=True)[:max_pages]]

def extract_contacts(html, page_url, dealer):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]): tag.decompose()
    matches = {}
    def record(email, ctx, evidence):
        email = clean_email(email)
        if not email: return
        context = " ".join(ctx.split())[:300]
        row = {"Sheet Row": dealer.get("Sheet Row", ""), "Source ID": dealer.get("Source ID", ""),
               "Dealership": dealer.get("Dealership", ""), "City": dealer.get("City", ""),
               "State": dealer.get("State", ""), "Website": dealer.get("Website", ""),
               "Email": email, "Context": context, "Role Hint": role_hint(context),
               "Evidence URL": page_url, "Evidence Type": evidence, "Discovered At UTC": utc_now(),
               "CRM Address Confirmed": "NO", "ADF Auto Send Authorized": "NO"}
        old = matches.get(email)
        if not old or (old["Evidence Type"] != "mailto" and evidence == "mailto"): matches[email] = row
    for anchor in soup.select('a[href^="mailto:"]'):
        href = anchor.get("href", "").split("?", 1)[0][7:]
        parent = anchor.find_parent(["li", "article", "section", "div", "p"]) or anchor
        record(href, parent.get_text(" ", strip=True)[:300], "mailto")
    for node in soup.find_all(string=True):
        s = str(node)
        if "@" not in s: continue
        parent = node.parent
        if not parent or parent.name in ("script", "style", "noscript"): continue
        around = parent.parent.get_text(" ", strip=True) if parent.parent else s
        for match in EMAIL.finditer(s[:1500]): record(match.group(), around[:300], "visible_text")
    return list(matches.values())

def inspect(row, max_pages=4, pause=1.0):
    url = url_clean(row.get("Website", ""))
    audit = {"Sheet Row": row.get("Sheet Row", ""), "Dealership": row.get("Dealership", ""),
             "Website": row.get("Website", ""), "Checked At UTC": utc_now(),
             "Pages Checked": 0, "Contacts Found": 0, "Status": "unknown", "Notes": ""}
    if not url:
        audit["Status"] = "missing_website"
        return [], audit
    try:
        html, home = fetch(url)
        audit["Pages Checked"] += 1
        if not same_site(url, home) and not identity_ok(row, html):
            audit["Status"] = "unverified_redirect"
            return [], audit
        out = extract_contacts(html, home, row)
        for link in link_candidates(html, home, max_pages=max_pages):
            if link == home: continue
            time.sleep(pause)
            try:
                page, final = fetch(link)
                if not same_site(home, final): continue
                audit["Pages Checked"] += 1
                out.extend(extract_contacts(page, final, row))
            except (FetchError, RobotsBlocked): continue
        out = list({(c["Email"], c["Evidence URL"]): c for c in out}.values())
        audit["Contacts Found"] = len(out)
        audit["Status"] = "contacts_found" if out else "no_public_email_found"
        return out, audit
    except (FetchError, RobotsBlocked) as exc:
        audit["Status"], audit["Notes"] = "blocked_or_unreachable", str(exc)[:180]
        return [], audit
    except Exception as exc:
        audit["Status"], audit["Notes"] = "error", (type(exc).__name__ + ": " + str(exc))[:180]
        return [], audit

def read_csv(path):
    if not Path(path).exists(): return []
    with open(path, newline="", encoding="utf-8-sig") as fh: return list(csv.DictReader(fh))

def save_csv(path, fields, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--start-row", type=int, default=1)
    p.add_argument("--state", default="")
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--output", default="local_contact_research.csv")
    p.add_argument("--audit", default="local_contact_audit.csv")
    args = p.parse_args()
    sources = sorted(glob.glob("data/dealers-*.csv"))
    if not sources: p.error("no data/dealers-*.csv files found")
    rows = [row for f in sources for row in read_csv(f)
            if row.get("Sheet Row", "").isdigit() and int(row["Sheet Row"]) >= args.start_row
            and (not args.state or row.get("State", "").upper() == args.state.upper())]
    rows.sort(key=lambda r: int(r["Sheet Row"]))
    prior_contacts, prior_audit = read_csv(args.output), read_csv(args.audit)
    previously_checked = {a.get("Sheet Row") for a in prior_audit}
    rows = [r for r in rows if r["Sheet Row"] not in previously_checked][:max(1, args.batch_size)]
    results, audits = list(prior_contacts), list(prior_audit)
    for i, row in enumerate(rows, 1):
        contacts, audit = inspect(row, pause=max(0.5, args.pause))
        results.extend(contacts)
        audits.append(audit)
        print(f"[{i}/{len(rows)}] {row['Dealership']}: {audit['Status']} ({len(contacts)} published contacts)", flush=True)
        save_csv(args.output, FIELDS, results)
        save_csv(args.audit, AUDIT_FIELDS, audits)
    if not rows: print("No unchecked dealerships in selected range.")

if __name__ == "__main__":
    main()

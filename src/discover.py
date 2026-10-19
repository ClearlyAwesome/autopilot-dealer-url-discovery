#!/usr/bin/env python3
"""Find verified dealer inventory landing pages; never scrape vehicle listings."""
import argparse
import concurrent.futures
import datetime as dt
import ipaddress
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

SHEET_ID = os.environ.get("SPREADSHEET_ID", "1tGBSFp-bxhW1_uocOU0uBinXtPos-TKPJLyNDmrNrX0")
MASTER = "Auto Pilot — Nationwide Dealership Inventory URL Directory"
AUDIT = "Discovery Audit"
PROGRESS = "Discovery Progress"
UA = "AutoPilotDirectoryResearch/1.0 (public dealership directory links)"
NEW = re.compile(r"(?:new[-_/](?:vehicles?|cars?|inventory|auto)|(?:vehicles?|cars?)[-_/]new|inventory[-_/]new|searchnew|search[-_/]new|[?&](?:type|condition)=new\b)", re.I)
USED = re.compile(r"(?:used[-_/](?:vehicles?|cars?|inventory|auto)|pre[-_]?owned[-_/](?:vehicles?|inventory|cars?)|(?:vehicles?|cars?)[-_/]used|inventory[-_/]used|searchused|search[-_/]used|[?&](?:type|condition)=used\b)", re.I)
NEW_LABEL = re.compile(r"\b(?:new (?:vehicles?|cars?|inventory)|shop new|browse new|view new)\b", re.I)
USED_LABEL = re.compile(r"\b(?:used (?:vehicles?|cars?|inventory)|pre[- ]owned (?:vehicles?|cars?|inventory)|shop used|browse used|view used)\b", re.I)
# Require explicit lease wording; generic specials, financing, and service offers are not lease pages.
LEASE = re.compile(r"(?:lease[-_/]?(?:specials?|offers?|deals?|incentives?)|(?:specials?|offers?|deals?|incentives?)[-_/]?lease|[?&](?:offer|special|type)=lease\b)", re.I)
LEASE_LABEL = re.compile(r"\b(?:lease (?:specials?|offers?|deals?|incentives?)|(?:specials?|offers?|deals?|incentives?) (?:on )?leases?|current lease (?:offers?|deals?)|leasing (?:specials?|offers?|deals?))\b", re.I)
LEASE_BAD = re.compile(r"(?:service|parts|maintenance|coupon|privacy|/vdp/|/vin/|/vehicle-details/|finance[-_/]?application|credit[-_/]?application)", re.I)
EXCLUDE = re.compile(r"(?:specials|offers|incentives|service|parts|appointment|finance|lease|privacy|coupon|rebate|trade[-_]?in|certified[-_]?only|/vdp/|/vin/|/vehicle-details/)", re.I)
RETRYABLE = {"homepage_error", "robots_unavailable", "fetch_error", "homepage_blocked"}
ROBOTS = {}
LOCK = threading.Lock()
LOCAL = threading.local()


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def url_clean(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.lower().startswith(("http://", "https://")):
        if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I):
            return ""
        raw = "https://" + raw
    try:
        p = urllib.parse.urlsplit(raw)
        host = (p.hostname or "").lower()
        if p.scheme not in ("http", "https") or not host or p.username or p.password:
            return ""
        if host in ("localhost",) or host.endswith((".local", ".internal", ".test")):
            return ""
        if p.port and p.port not in (80, 443):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))
    except ValueError:
        return ""


def host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def same_site(a, b):
    x, y = host(a), host(b)
    return bool(x and y and (x == y or x.endswith("." + y) or y.endswith("." + x)))


def session():
    if not hasattr(LOCAL, "s"):
        LOCAL.s = requests.Session()
        LOCAL.s.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.7"})
    return LOCAL.s


class FetchError(Exception):
    pass


class RobotsBlocked(FetchError):
    pass


def robots_allowed(url):
    p = urllib.parse.urlsplit(url)
    root = p.scheme + "://" + p.netloc
    with LOCK:
        cached = ROBOTS.get(root)
    if cached is None:
        try:
            r = session().get(root + "/robots.txt", timeout=(4, 6))
            if r.status_code in (401, 403):
                cached = False
            elif r.status_code in (404, 410):
                cached = True
            elif r.status_code == 429 or r.status_code >= 500:
                raise FetchError("robots HTTP " + str(r.status_code))
            elif r.ok:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(r.text[:200000].splitlines())
                cached = parser
            else:
                cached = True
        except requests.RequestException as exc:
            raise FetchError("robots unavailable: " + str(exc)[:100]) from exc
        with LOCK:
            ROBOTS[root] = cached
    return cached if isinstance(cached, bool) else cached.can_fetch(UA, url)


def fetch(url, max_bytes=1200000):
    url = url_clean(url)
    if not url:
        raise FetchError("invalid URL")
    # Validate each redirect and robots policy before requesting the next hop.
    for _ in range(5):
        if not robots_allowed(url):
            raise RobotsBlocked("robots disallow")
        try:
            with session().get(url, timeout=(4, 9), stream=True, allow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    target = url_clean(urllib.parse.urljoin(url, r.headers.get("Location", "")))
                    if not target:
                        raise FetchError("invalid redirect")
                    url = target
                    continue
                if r.status_code >= 400:
                    raise FetchError("HTTP " + str(r.status_code))
                typ = r.headers.get("Content-Type", "").lower()
                if not any(t in typ for t in ("text/html", "xml", "text/plain", "application/xhtml")):
                    raise FetchError("unsupported content type")
                chunks = bytearray()
                for chunk in r.iter_content(32768):
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        raise FetchError("response too large")
                return chunks.decode(r.encoding or "utf-8", errors="replace"), url
        except requests.RequestException as exc:
            raise FetchError(str(exc)[:140]) from exc
    raise FetchError("too many redirects")


def score(url, label, kind):
    path = urllib.parse.urlsplit(url).path + "?" + urllib.parse.urlsplit(url).query
    if kind == "lease":
        if LEASE_BAD.search(path):
            return 0
        return 8 * bool(LEASE.search(path)) + 5 * bool(LEASE_LABEL.search(label))
    if EXCLUDE.search(path):
        return 0
    if kind == "new":
        if USED.search(path) or USED_LABEL.search(label):
            return 0
        return 7 * bool(NEW.search(path)) + 5 * bool(NEW_LABEL.search(label))
    if NEW.search(path) or NEW_LABEL.search(label):
        return 0
    return 7 * bool(USED.search(path)) + 5 * bool(USED_LABEL.search(label))


def links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        u = url_clean(urllib.parse.urljoin(base, a["href"]))
        if not u or not same_site(base, u):
            continue
        label = a.get_text(" ", strip=True)[:150]
        if max(score(u, label, "new"), score(u, label, "used"), score(u, label, "lease")) >= 5:
            out.append((u, label, "homepage navigation"))
    return out


def sitemap_links(base):
    p = urllib.parse.urlsplit(base)
    root = p.scheme + "://" + p.netloc
    try:
        xml, final = fetch(root + "/sitemap.xml", 2500000)
        if not same_site(base, final):
            return []
        tree = ET.fromstring(xml)
        locs = [e.text.strip() for e in tree.iter() if e.tag.endswith("loc") and e.text]
        urls = [x for x in locs if not x.lower().endswith(".xml")]
        maps = [x for x in locs if x.lower().endswith(".xml") and re.search("inventory|vehicle|page|lease|special|offer", x, re.I)][:2]
        for child in maps:
            if not url_clean(child) or not same_site(base, child):
                continue
            try:
                sub, _ = fetch(child, 2500000)
                urls.extend(e.text.strip() for e in ET.fromstring(sub).iter() if e.tag.endswith("loc") and e.text)
            except (FetchError, ET.ParseError):
                pass
        return [(url_clean(u), "", "sitemap") for u in urls[:4000] if url_clean(u) and same_site(base, u)]
    except (FetchError, ET.ParseError):
        return []


def identity_ok(row, html):
    soup = BeautifulSoup(html[:150000], "html.parser")
    headline = " ".join(e.get_text(" ", strip=True) for e in soup.find_all(["title", "h1"], limit=8)).lower()
    city = row.get("City", "").strip().lower()
    name = row.get("Dealership", "").lower()
    common = {"auto", "cars", "ford", "chevrolet", "toyota", "nissan", "honda", "kia", "buick", "jeep", "dodge", "lincoln", "motors", "chrysler", "volkswagen", "gmc", "dealership"}
    tokens = [x for x in re.findall(r"[a-z]{4,}", name) if x not in common]
    return bool(tokens and ((city and city in headline and any(t in headline for t in tokens)) or sum(t in headline for t in tokens) >= 2))


def page_ok(html, url, kind):
    soup = BeautifulSoup(html[:250000], "html.parser")
    title = " ".join(e.get_text(" ", strip=True) for e in soup.find_all(["title", "h1"], limit=5))
    if re.search(r"404|page not found|domain for sale|website for sale|access denied", title, re.I):
        return False
    if kind == "lease":
        # A verified page must be explicitly lease-focused; a generic "Specials" page is insufficient.
        return bool(LEASE.search(url) and (LEASE_LABEL.search(title) or re.search(r"\blease\b", title, re.I))
                    or LEASE_LABEL.search(title))
    return score(url, title, kind) >= 7 or bool((NEW_LABEL if kind == "new" else USED_LABEL).search(title))


@dataclass
class Result:
    number: int
    row: dict
    checked: str
    status: str = "fetch_error"
    new: str = ""
    used: str = ""
    lease: str = ""
    new_evidence: str = ""
    used_evidence: str = ""
    lease_evidence: str = ""
    final_home: str = ""
    notes: str = ""

    def audit(self):
        return [str(self.number), self.row.get("Source ID", ""), self.row.get("Dealership", ""),
                self.row.get("Website", ""), self.checked, self.status, self.new, self.used,
                self.new_evidence, self.used_evidence, self.final_home, self.notes,
                self.lease, self.lease_evidence, self.checked]


def inspect(number, row):
    out = Result(number, row, now_utc().isoformat(timespec="seconds"))
    home = url_clean(row.get("Website", ""))
    if not home:
        out.status = "missing_website"
        return out
    try:
        html, actual = fetch(home)
        out.final_home = actual
        if not same_site(home, actual) and not identity_ok(row, html):
            out.status = "redirect_mismatch"
            return out
        found = links(html, actual)
        if any(not row.get(column) and not any(score(u, label, kind) for u, label, _ in found)
               for column, kind in (("New Inventory URL", "new"), ("Used Inventory URL", "used"),
                                    ("Lease Specials URL", "lease"))):
            found.extend(sitemap_links(actual))
        for kind in ("new", "used", "lease"):
            column = {"new": "New Inventory URL", "used": "Used Inventory URL", "lease": "Lease Specials URL"}[kind]
            if row.get(column):
                continue
            candidates = sorted(((score(u, label, kind), u, evidence) for u, label, evidence in found
                                 if score(u, label, kind) >= 5), reverse=True)
            tried = set()
            for _, candidate, evidence in candidates:
                if candidate in tried:
                    continue
                tried.add(candidate)
                if len(tried) > 3:
                    break
                try:
                    time.sleep(.3)
                    content, final = fetch(candidate)
                    if same_site(actual, final) and page_ok(content, final, kind):
                        if kind == "new":
                            out.new, out.new_evidence = final, evidence + "; live category page"
                        elif kind == "used":
                            out.used, out.used_evidence = final, evidence + "; live category page"
                        else:
                            out.lease, out.lease_evidence = final, evidence + "; live lease specials page"
                        break
                except FetchError:
                    continue
        if out.new and out.new == out.used:
            out.new = out.used = ""
            out.notes = "New and used resolved to same URL"
        has_new = bool(row.get("New Inventory URL") or out.new)
        has_used = bool(row.get("Used Inventory URL") or out.used)
        out.status = "found_both" if has_new and has_used else "found_new_only" if has_new else "found_used_only" if has_used else "found_lease_only" if (row.get("Lease Specials URL") or out.lease) else "no_matching_links"
    except RobotsBlocked as exc:
        out.status, out.notes = "robots_blocked", str(exc)
    except FetchError as exc:
        msg = str(exc)
        out.status = "robots_unavailable" if "robots" in msg else "homepage_blocked" if re.search(r"HTTP (401|403|429)", msg) else "homepage_error"
        out.notes = msg[:180]
    except Exception as exc:
        out.status, out.notes = "fetch_error", (type(exc).__name__ + ": " + str(exc))[:180]
    return out


class Sheets:
    def __init__(self):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not raw:
            raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON repository secret")
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.http = AuthorizedSession(credentials)
        self.base = "https://sheets.googleapis.com/v4/spreadsheets/" + SHEET_ID

    def call(self, method, path, **kwargs):
        response = self.http.request(method, self.base + path, timeout=50, **kwargs)
        if not response.ok:
            raise RuntimeError("Sheets API HTTP " + str(response.status_code) + ": " + response.text[:400])
        return response.json()

    def read(self, tab, area):
        ref = urllib.parse.quote("'" + tab + "'!" + area, safe="")
        return self.call("GET", "/values/" + ref, params={"valueRenderOption": "FORMATTED_VALUE"}).get("values", [])

    def write(self, ranges):
        if ranges:
            self.call("POST", "/values:batchUpdate", json={"valueInputOption": "RAW", "data": ranges})

    def append(self, rows):
        if rows:
            ref = urllib.parse.quote("'" + AUDIT + "'!A:L", safe="")
            self.call("POST", "/values/" + ref + ":append",
                      params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                      json={"values": rows})


def eligible(number, row, history, now, lease_checked=False):
    # Backfill previously researched dealers after lease support is introduced.
    if not row.get("Lease Specials URL") and not lease_checked:
        return True
    if row.get("New Inventory URL") and row.get("Used Inventory URL"):
        return False
    past = history.get(number, [])
    if not past:
        return True
    if len(past) >= 3:
        return False
    stamp, status = past[-1]
    try:
        hours = (now - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 3600
    except (ValueError, TypeError):
        hours = 10000
    if status in RETRYABLE:
        return hours >= 48
    if status in ("found_new_only", "found_used_only"):
        return hours >= 168 and len(past) < 2
    if status == "no_matching_links":
        return hours >= 720 and len(past) < 2
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--report", default="run-report.json")
    args = parser.parse_args()
    sheets = Sheets()
    table = sheets.read(MASTER, "A1:M19172")
    expected = ["Source ID", "Dealership", "Website", "New Inventory URL", "Used Inventory URL"]
    if not table or table[0][:5] != expected:
        raise RuntimeError("Unexpected master sheet columns; refusing to write")
    rows = [(n, dict(zip(table[0], [str(x) for x in raw]))) for n, raw in enumerate(table[1:], 2)
            if len(raw) > 1 and str(raw[1]).strip()]
    history = defaultdict(list)
    for record in sheets.read(AUDIT, "A2:F"):
        if len(record) >= 6:
            try:
                history[int(record[0])].append((record[4], record[5]))
            except ValueError:
                pass
    now = now_utc()
    pending = [(n, row) for n, row in rows if eligible(n, row, history, now)]
    pending.sort(key=lambda item: item[0] in history)
    batch = pending[:min(250, max(1, args.batch_size))]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, max(1, args.workers))) as pool:
        futures = [pool.submit(inspect, n, row) for n, row in batch]
        for f in concurrent.futures.as_completed(futures):
            result = f.result()
            results.append(result)
            print(str(result.number) + " " + result.status, flush=True)
    changes = []
    count_new = count_used = 0
    for result in results:
        old = result.row
        new = old.get("New Inventory URL") or result.new
        used = old.get("Used Inventory URL") or result.used
        if new != old.get("New Inventory URL", "") or used != old.get("Used Inventory URL", ""):
            changes.append({"range": "'" + MASTER + "'!D" + str(result.number) + ":E" + str(result.number),
                            "values": [[new, used]]})
            count_new += bool(result.new and not old.get("New Inventory URL"))
            count_used += bool(result.used and not old.get("Used Inventory URL"))
    for i in range(0, len(changes), 80):
        sheets.write(changes[i:i + 80])
    sheets.append([x.audit() for x in sorted(results, key=lambda x: x.number)])
    summary = {"run_utc": now.isoformat(timespec="seconds"), "total_dealerships": len(rows),
               "batch_checked": len(results), "new_urls_added": count_new,
               "used_urls_added": count_used, "status_counts": dict(Counter(x.status for x in results))}
    progress = [{"range": "'" + PROGRESS + "'!B9:B13",
                 "values": [[summary["run_utc"]], ["Active — batch completed" if results else "No eligible rows"],
                            [len(results)], [count_new], [count_used]]}]
    if os.environ.get("GITHUB_REPOSITORY"):
        progress.append({"range": "'" + PROGRESS + "'!B14",
                         "values": [["https://github.com/" + os.environ["GITHUB_REPOSITORY"]]]})
    sheets.write(progress)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as f:
        f.write("## Auto Pilot URL discovery\n\n")
        for key, value in summary.items():
            f.write("- **" + key + "**: " + str(value) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

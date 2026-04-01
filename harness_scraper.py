#!/usr/bin/env python3
"""
Harness Racing Data Scraper v2 - harness.com.au
Intercepts the site's own API calls for reliable data extraction.

Usage:
    python harness_scraper.py --horse goodtime-oscar
    python harness_scraper.py --trainer tammy-langley
    python harness_scraper.py --driver grace-jones
    python harness_scraper.py --bulk horses.txt
"""

import argparse
import json
import re as _re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BASE_URL = "https://www.harness.au"
DELAY = 2.0
HEADLESS = True  # set to False via --visible flag

HEADER_FILL = PatternFill("solid", start_color="1A3A5C")
HEADER_FONT = Font(bold=True, color="FFFFFF", name="Arial", size=10)
ALT_FILL    = PatternFill("solid", start_color="EAF0F8")
TITLE_FONT  = Font(bold=True, name="Arial", size=14, color="1A3A5C")
META_FONT   = Font(italic=True, name="Arial", size=9, color="888888")
CELL_FONT   = Font(name="Arial", size=9)
THIN_BORDER = Border(
    bottom=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="EEEEEE"),
)


# ── Page fetcher ──────────────────────────────────────────────────────────────

def fetch_page_data(url: str, headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright
    from bs4 import BeautifulSoup

    api_responses = []
    all_requests  = []
    print(f"  → Loading: {url}")
    if not headless:
        print("  → Browser window will open — watch it load, then it will close automatically.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )

        # Mask automation flags
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        page = context.new_page()

        # Capture ALL network responses (not just JSON)
        def handle_response(response):
            try:
                req_url = response.url
                status  = response.status
                ct      = response.headers.get("content-type", "")
                all_requests.append(f"[{status}] {req_url}")

                if status == 200 and "json" in ct:
                    try:
                        data = response.json()
                        api_responses.append({"url": req_url, "data": data})
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", handle_response)

        # Navigate
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"  ⚠ Navigation note: {e}")

        # Wait progressively — some sites load data lazily
        print("  → Waiting for data to load (up to 15s)...")
        for i in range(5):
            time.sleep(3)
            count = len(api_responses)
            print(f"     {i*3+3}s — {count} API responses so far, {len(all_requests)} total requests")
            if count > 5:
                break  # enough data, stop waiting

        # Scroll to trigger lazy loads
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(1)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        # Try load-more buttons
        for selector in ["button:has-text('Load More')", "button:has-text('Show All')",
                         ".load-more", "[class*='loadmore']", "a:has-text('More results')"]:
            try:
                if page.locator(selector).count() > 0:
                    page.locator(selector).first.click()
                    time.sleep(2)
                    print(f"  → Clicked '{selector}' button")
            except Exception:
                pass

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")

    # Save debug files to output folder
    debug_dir = Path("output/debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    slug = url.split("/")[-1]

    # Save raw HTML so we can inspect it
    html_path = debug_dir / f"{slug}_page.html"
    html_path.write_text(html, encoding="utf-8")

    # Save all network requests list
    net_path = debug_dir / f"{slug}_network.txt"
    net_path.write_text("\n".join(all_requests), encoding="utf-8")

    # Save captured API JSON
    api_path = debug_dir / f"{slug}_api.json"
    api_path.write_text(
        json.dumps([{"url": r["url"], "keys": list(r["data"].keys()) if isinstance(r["data"], dict) else type(r["data"]).__name__}
                    for r in api_responses], indent=2),
        encoding="utf-8"
    )

    print(f"  → {len(api_responses)} JSON responses | {len(all_requests)} total requests | HTML: {len(html)} bytes")
    print(f"  → Debug files saved to: output/debug/{slug}_*.* ")

    return {"api_responses": api_responses, "all_requests": all_requests, "soup": soup}


# ── API base ──────────────────────────────────────────────────────────────────

API_BASE = "https://api.harness.au"

# Shared browser session — reuse across multiple scrapes
_browser_session = None

def get_browser_session():
    """Get or create a persistent browser context with cookies."""
    global _browser_session
    if _browser_session is None:
        from playwright.sync_api import sync_playwright
        print("  → Starting browser session...")
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        _browser_session = (pw, browser, context)
    return _browser_session


def close_browser_session():
    global _browser_session
    if _browser_session:
        pw, browser, context = _browser_session
        browser.close()
        pw.stop()
        _browser_session = None


def scrape_via_browser(slug: str, entity_type: str = "horse") -> dict:
    """
    Load the page in a real browser and intercept all API JSON responses.
    Uses request routing to ensure we catch everything before the page loads.
    """
    _, _, context = get_browser_session()
    page_url = f"{BASE_URL}/{entity_type}/{slug}"
    captured = {}

    print(f"  → Browser loading: {page_url}")

    page = context.new_page()

    # Use route to intercept API calls — fires before response event
    def handle_route(route):
        response = route.fetch()
        try:
            ct = response.headers.get("content-type", "")
            if response.status == 200 and "json" in ct and API_BASE in route.request.url:
                try:
                    captured[route.request.url] = response.json()
                except Exception:
                    pass
        except Exception:
            pass
        route.fulfill(response=response)

    page.route("**/*", handle_route)

    try:
        page.goto(page_url, timeout=60000, wait_until="networkidle")
    except Exception as e:
        print(f"  ⚠ Navigation note: {e}")

    # Extra wait for any deferred loads
    time.sleep(3)

    page.close()
    print(f"  → Captured {len(captured)} API responses")
    for url in captured:
        print(f"     {url}")
    return captured


def get_captured(captured: dict, keyword: str):
    """Find the first captured response whose URL contains keyword."""
    for url, data in captured.items():
        if keyword in url.lower():
            return data
    return None


def fetch_extra_pages(captured_url: str, context, total_pages: int) -> list:
    """Fetch additional result pages by reusing the browser context."""
    import requests as req
    # Extract cookies from browser for authenticated requests
    _, _, ctx = get_browser_session()
    cookies = {c["name"]: c["value"] for c in ctx.cookies(BASE_URL)}

    rows = []
    for page_num in range(2, min(total_pages + 1, 11)):
        # Build URL with updated page param
        base = captured_url.split("?")[0]
        url = f"{base}?pagination%5Blimit%5D=100&pagination%5Bpage%5D={page_num}"
        try:
            r = req.get(url, cookies=cookies, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": BASE_URL + "/",
            }, timeout=15)
            if r.status_code == 200:
                data = r.json()
                page_rows = _get_rows(data)
                rows.extend(page_rows)
                print(f"  → Page {page_num}/{total_pages}: {len(page_rows)} rows")
            time.sleep(0.5)
        except Exception as e:
            print(f"  ⚠ Page {page_num} error: {e}")
            break
    return rows



# ── Horse scraper ─────────────────────────────────────────────────────────────

def extract_horse_data(slug: str, headless: bool = True) -> dict:
    print(f"\n🐴 Scraping horse: {slug}")
    data = {"url": f"{BASE_URL}/horse/{slug}", "slug": slug,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M")}

    # Load page and intercept all API calls
    captured = scrape_via_browser(slug, "horse")

    # ── Profile ──
    raw_profile = get_captured(captured, f"/racing/horses/{slug}")
    # Make sure we don't accidentally grab the performance-records endpoint
    if raw_profile is None:
        for url, d in captured.items():
            if f"horses/{slug}" in url and "performance" not in url:
                raw_profile = d
                break
    profile = _flatten(raw_profile) if isinstance(raw_profile, dict) else {}

    # ── Results (first page captured by browser) ──
    results_url = None
    raw_results_p1 = None
    for url, d in captured.items():
        if "performance-records" in url:
            raw_results_p1 = d
            results_url = url
            break

    raw_results = _get_rows(raw_results_p1) if raw_results_p1 else []
    print(f"  → Page 1: {len(raw_results)} result rows")

    # Fetch remaining pages using browser cookies
    if raw_results_p1 and isinstance(raw_results_p1, dict):
        total_pages = (raw_results_p1.get("pagination") or {}).get("pages", 1)
        if total_pages > 1 and results_url:
            print(f"  → Fetching {total_pages - 1} more page(s)...")
            raw_results += fetch_extra_pages(results_url, None, total_pages)

    # ── Upcoming — pull from the raw (unflattened) profile response ──
    upcoming_raw = []
    raw_horse_resp = get_captured(captured, f"/racing/horses/{slug}")
    if isinstance(raw_horse_resp, dict):
        upcoming_raw = raw_horse_resp.get("upcoming_races", [])
        if not isinstance(upcoming_raw, list):
            upcoming_raw = []
        # upcoming_races may itself be a dict with a list inside
        if isinstance(upcoming_raw, dict):
            upcoming_raw = list(upcoming_raw.values())[0] if upcoming_raw else []

    # ── Stats ──
    stats = {}
    for prefix in ["lifetime_summary", "current_season_summary", "last_season_summary"]:
        section = {k.replace(f"{prefix}.", ""): v
                   for k, v in profile.items() if k.startswith(prefix)}
        if section:
            stats[prefix.replace("_summary","").replace("_"," ").title()] = section

    # ── Pedigree ──
    pedigree = {}
    for field, label in [("horse.sire.name", "Sire"), ("horse.dam.name", "Dam"),
                          ("horse.bm_sire.name", "Broodmare Sire")]:
        if field in profile and profile[field]:
            pedigree[label] = profile[field]

    data["profile"]  = profile
    data["results"]  = _norm(raw_results)
    data["upcoming"] = _norm(upcoming_raw)
    data["stats"]    = stats
    data["pedigree"] = pedigree
    data["_api_urls"] = list(captured.keys())

    print(f"  → Profile: {len(profile)} fields | Results: {len(data['results'])} rows | Upcoming: {len(data['upcoming'])} rows")
    return data


# ── Person scraper ────────────────────────────────────────────────────────────

def extract_person_data(slug: str, entity_type: str, headless: bool = True) -> dict:
    api_type = entity_type + "s"   # trainers / drivers
    print(f"\n{'👤' if entity_type=='trainer' else '🎽'} Scraping {entity_type}: {slug}")

    data = {"url": f"{BASE_URL}/{entity_type}/{slug}", "slug": slug, "type": entity_type,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M")}

    captured = scrape_via_browser(slug, entity_type)

    # Profile — matches /trainers/profile/{slug} or /drivers/profile/{slug}
    raw_profile = None
    for url, d in captured.items():
        if f"/{api_type}/profile/{slug}" in url or (f"/{api_type}/{slug}" in url and "runs" not in url and "horses" not in url):
            raw_profile = d
            break
    profile = _flatten(raw_profile) if isinstance(raw_profile, dict) else {}

    # Results — matches /trainers/runs/{slug} or /drivers/runs/{slug}
    results_url = None
    raw_results_p1 = None
    for url, d in captured.items():
        if f"/{api_type}/runs/{slug}" in url or "performance-records" in url:
            raw_results_p1 = d
            results_url = url
            break
    results = _get_rows(raw_results_p1) if raw_results_p1 else []

    # Top horses (trainer only)
    top_horses = []
    if raw_results_p1 and isinstance(raw_results_p1, dict):
        top_horses = raw_results_p1.get("top_horses", []) or []

    if raw_results_p1 and isinstance(raw_results_p1, dict):
        total_pages = (raw_results_p1.get("pagination") or {}).get("pages", 1)
        if total_pages > 1 and results_url:
            results += fetch_extra_pages(results_url, None, total_pages)

    # Horses in stable (trainer only)
    horses = []
    if entity_type == "trainer":
        for url, d in captured.items():
            if "horses" in url and slug in url:
                horses = _get_rows(d)
                break

    # Stats
    stats = {}
    for prefix in ["lifetime_summary", "current_season_summary", "last_season_summary"]:
        section = {k.replace(f"{prefix}.", ""): v
                   for k, v in profile.items() if k.startswith(prefix)}
        if section:
            stats[prefix.replace("_summary","").replace("_"," ").title()] = section

    data["profile"] = profile
    data["results"] = _norm(results)
    data["horses_in_stable"] = _norm(horses)
    data["top_horses"] = _norm(top_horses)
    data["stats"] = stats
    data["_api_urls"] = list(captured.keys())

    print(f"  → Profile: {len(profile)} fields | Results: {len(data['results'])} rows")
    return data




# ── HTML fallback parsers ─────────────────────────────────────────────────────

def _html_profile(soup) -> dict:
    profile = {}
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        for i in range(0, len(cells)-1, 2):
            k = _t(cells[i]).rstrip(":")
            v = _t(cells[i+1])
            if k and len(k) < 50:
                profile[k] = v
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            profile[_t(dt).rstrip(":")] = _t(dd)
    return {k: v for k, v in profile.items() if k and v}


def _html_table(soup) -> list:
    best, best_score = None, 0
    for tbl in soup.find_all("table"):
        rows = len(tbl.find_all("tr"))
        score = rows * 0.1
        if score > best_score:
            best_score, best = score, tbl
    if not best:
        return []
    hdrs = [_t(h) for h in best.select("thead th, thead td")]
    if not hdrs:
        fr = best.select_one("tr")
        hdrs = [_t(c) for c in fr.find_all(["th","td"])] if fr else []
    out = []
    for row in best.select("tbody tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        rd = {hdrs[i] if i < len(hdrs) else f"Col_{i+1}": _t(c) for i, c in enumerate(cells)}
        if any(rd.values()):
            out.append(rd)
    return out


def _html_upcoming(soup) -> list:
    for section in soup.find_all(["section", "div"]):
        if "upcoming" in section.get_text().lower():
            tbl = section.find("table")
            if tbl:
                from bs4 import BeautifulSoup as BS
                return _html_table(BS(str(tbl), "html.parser"))
    return []


# ── Utilities ─────────────────────────────────────────────────────────────────

def _t(el) -> str:
    return el.get_text(strip=True) if el else ""


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        # For futurity_eligibility, only keep the human-readable title
        if k == "futurity_eligibility" and isinstance(v, dict):
            out[key] = v.get("title", "")
        elif isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, list):
            out[key] = ", ".join(str(i) for i in v)
        else:
            out[key] = str(v) if v is not None else ""
    return out


def _get_rows(d) -> list:
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d
    if isinstance(d, dict):
        for k in ["performance_records", "previous_runs", "runs", "data", "results",
                  "items", "rows", "records", "list", "races", "horses"]:
            if k in d and isinstance(d[k], list):
                return d[k]
    return []


def _norm(rows: list) -> list:
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append({k: str(v) if v is not None else "" for k, v in _flatten(row).items()})
    return out


# ── Excel export ──────────────────────────────────────────────────────────────

def _hdr(ws, r, c, val):
    cell = ws.cell(r, c, val)
    cell.fill = HEADER_FILL
    cell.font = HEADER_FONT
    cell.alignment = Alignment(horizontal="left", vertical="center")
    cell.border = THIN_BORDER
    ws.row_dimensions[r].height = 22


def _title(ws, text, cols=10):
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30
    ws.merge_cells(f"A1:{get_column_letter(cols)}1")


def _write_df(ws, df: pd.DataFrame, start_row=1):
    if df.empty:
        ws.cell(start_row, 1, "No data found.").font = META_FONT
        return
    cols = list(df.columns)
    for c, col in enumerate(cols, 1):
        _hdr(ws, start_row, c, col)
    for ro, (_, row) in enumerate(df.iterrows(), 1):
        r = start_row + ro
        for c, col in enumerate(cols, 1):
            cell = ws.cell(r, c, row[col])
            cell.font = CELL_FONT
            cell.alignment = Alignment(vertical="center")
            cell.border = THIN_BORDER
            if r % 2 == 0:
                cell.fill = ALT_FILL
        ws.row_dimensions[r].height = 16
    for c, col in enumerate(cols, 1):
        w = max(len(str(col)), df[col].astype(str).str.len().max() if not df.empty else 0)
        ws.column_dimensions[get_column_letter(c)].width = min(w + 3, 45)
    ws.freeze_panes = ws.cell(start_row + 1, 1)
    ws.auto_filter.ref = (f"{ws.cell(start_row,1).coordinate}:"
                          f"{ws.cell(start_row+len(df), len(cols)).coordinate}")


def _friendly_name(profile: dict, slug: str) -> str:
    """Get horse/person display name from profile."""
    for key in ["horse.formatted_name", "formatted_name", "display_name",
                "name", "Name", "full_name"]:
        if profile.get(key):
            return profile[key]
    return slug.replace("-", " ").title()


def _clean_profile(profile: dict) -> dict:
    """Remove internal/junk fields, rename to human-friendly keys."""
    skip = {"meta.status", "meta.code", "placements.skyscraper", "placements.sidebar",
            "placements.header", "placements.footer", "horse.slug", "horse.owner.rise_api_id",
            "horse.trainer.rise_api_id", "horse.owner.slug", "horse.trainer.slug",
            "horse.sire.slug", "horse.dam.slug", "horse.bm_sire.slug",
            "pagination.limit", "pagination.page", "pagination.page_start",
            "pagination.page_end", "pagination.total", "pagination.pages",
            "performance_records", "horse.futurity_eligibility",
            "trainer.primary_silk.silk_image.filename", "trainer.primary_silk.silk_image.path",
            "trainer.secondary_silk.silk_image.filename", "trainer.secondary_silk.silk_image.path",
            "trainer.tertiary_silk.silk_image"}
    # Any key starting with these prefixes should be skipped entirely
    skip_prefixes = ("performance_records.", "upcoming_races.", "placements.",
                     "trainer.primary_silk.", "trainer.secondary_silk.", "trainer.tertiary_silk.")
    rename = {
        "horse.formatted_name": "Name",
        "horse.gender": "Gender",
        "horse.colour": "Colour",
        "horse.gait": "Gait",
        "horse.freezebrand": "Freeze Brand",
        "horse.microchip": "Microchip",
        "horse.foaling_date": "Foaling Date",
        "horse.hwoe_assessment": "HWOE / Class",
        "horse.assessment": "Rating",
        "horse.breeder": "Breeder",
        "horse.deceased": "Deceased",
        "horse.sire.name": "Sire",
        "horse.dam.name": "Dam",
        "horse.bm_sire.name": "Broodmare Sire",
        "horse.trainer.display_name": "Trainer",
        "horse.owner.display_name": "Owner",
        "lifetime_summary.starts": "Lifetime Starts",
        "lifetime_summary.wins": "Lifetime Wins",
        "lifetime_summary.seconds": "Lifetime 2nds",
        "lifetime_summary.thirds": "Lifetime 3rds",
        "lifetime_summary.best_win_mile_rate": "Lifetime Best WMR",
        "lifetime_summary.stakes": "Lifetime Stakes ($)",
        "current_season_summary.starts": "This Season Starts",
        "current_season_summary.wins": "This Season Wins",
        "current_season_summary.seconds": "This Season 2nds",
        "current_season_summary.thirds": "This Season 3rds",
        "current_season_summary.best_win_mile_rate": "This Season Best WMR",
        "current_season_summary.stakes": "This Season Stakes ($)",
        "last_season_summary.starts": "Last Season Starts",
        "last_season_summary.wins": "Last Season Wins",
        "last_season_summary.seconds": "Last Season 2nds",
        "last_season_summary.thirds": "Last Season 3rds",
        "last_season_summary.best_win_mile_rate": "Last Season Best WMR",
        "last_season_summary.stakes": "Last Season Stakes ($)",
    }
    out = {}
    for k, v in profile.items():
        if k in skip:
            continue
        if any(k.startswith(p) for p in skip_prefixes):
            continue
        if v is None or v == "" or v == "None":
            continue
        # For futurity_eligibility, extract just the title
        if k == "horse.futurity_eligibility" and isinstance(v, dict):
            v = v.get("title", str(v))
        # Skip any remaining raw nested dicts or lists (junk data)
        if isinstance(v, (dict, list)):
            continue
        label = rename.get(k, k)
        out[label] = v
    return out


def _clean_results(rows: list) -> pd.DataFrame:
    """Rename raw API result fields to readable column names."""
    if not rows:
        return pd.DataFrame()
    rename = {
        "formatted_handicap": "Handicap",
        "place": "Place",
        "margin": "Margin",
        "prizemoney": "Prize Money",
        "stewards_comments_short": "Stewards (Short)",
        "stewards_comments_long": "Stewards Notes",
        "stewards_comment": "Stewards (Short)",
        "win_mile_rate": "WMR",
        "race.time_mile_rate": "Race MR",
        "meeting.date": "Date",
        "track.desktop_display_name": "Track",
        "race.meeting.track.name": "Track",
        "race.meeting.date": "Date",
        "race.name": "Race Name",
        "race.number": "Race No",
        "race.distance": "Distance (m)",
        "race.class": "Class",
        "race.track_condition": "Track Condition",
        "race.start_type": "Start Type",
        "driver.display_name": "Driver",
        "trainer.display_name": "Trainer",
        "barrier": "Barrier",
        "starting_price": "SP",
        "race.meeting.track.state": "State",
    }
    flat_rows = [_flatten(r) for r in rows]
    df = pd.DataFrame(flat_rows)
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    # Reorder: most useful columns first, stewards notes last (it's long)
    priority = ["Date", "Track", "State", "Race No", "Race Name", "Distance (m)",
                "Start Type", "Class", "Track Condition", "Barrier", "Handicap",
                "Place", "Margin", "Race MR", "WMR", "SP", "Driver", "Trainer",
                "Prize Money", "Stewards (Short)", "Stewards Notes"]
    ordered = [c for c in priority if c in df.columns]
    rest = [c for c in df.columns if c not in ordered]
    return df[ordered + rest]


def export_horse_csv(data: dict, out_dir: str):
    """Export horse data as CSV files — opens directly in Google Sheets."""
    slug = data["slug"]
    profile_raw = data.get("profile", {})
    name = _friendly_name(profile_raw, slug)
    folder = Path(out_dir) / slug
    folder.mkdir(parents=True, exist_ok=True)

    # Profile
    profile_clean = _clean_profile(profile_raw)
    if profile_clean:
        df_p = pd.DataFrame(list(profile_clean.items()), columns=["Field", "Value"])
        df_p.to_csv(folder / "1_profile.csv", index=False)
        print(f"  → Profile: {len(df_p)} rows")

    # Race Results
    df_r = _clean_results(data.get("results", []))
    if not df_r.empty:
        df_r.to_csv(folder / "2_race_results.csv", index=False)
        print(f"  → Race Results: {len(df_r)} rows")

    # Stats
    stats = data.get("stats", {})
    if stats:
        rows = []
        for section, vals in stats.items():
            for k, v in vals.items():
                rows.append({"Section": section, "Stat": k.replace("_"," ").title(), "Value": v})
        pd.DataFrame(rows).to_csv(folder / "3_stats.csv", index=False)

    # Upcoming
    upcoming = data.get("upcoming", [])
    if upcoming:
        _clean_results(upcoming).to_csv(folder / "4_upcoming.csv", index=False)

    # Pedigree
    ped = data.get("pedigree", {})
    if ped:
        pd.DataFrame(list(ped.items()), columns=["Relation","Name"]).to_csv(
            folder / "5_pedigree.csv", index=False)

    print(f"  ✅ CSV files saved to: {folder}")
    print(f"     → Import each into Google Sheets via File → Import → Upload")


def get_sheets_client():
    """Authenticate as YOU (not a service account) using OAuth — uses your own Drive."""
    try:
        import gspread
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
    except ImportError:
        print("  ⚠ Run: python -m pip install gspread google-auth google-auth-oauthlib")
        return None

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    token_path = Path("token.json")
    creds = None

    # Reuse saved login if available
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    # If no valid login, open browser to log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path("credentials.json").exists():
                print("  ⚠ credentials.json not found.")
                print("     See setup: console.cloud.google.com → APIs & Services → Credentials")
                print("     Create OAuth 2.0 Client ID (Desktop app) and download as credentials.json")
                return None
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", scopes)
            print("  → Opening browser for Google login (one-time only)...")
            creds = flow.run_local_server(port=0)

        token_path.write_text(creds.to_json())
        print("  → Login saved — won't need to log in again")

    return gspread.authorize(creds)


DRIVE_FOLDER_ID = "1LVw4zyJSGGYhh6FOqYU4eRMVMFXRLUd9"

# Files to sync to Drive after scrape commands
DRIVE_SYNC_FILES = [
    "stride_profiles.csv",
    "stride_results.csv",
    "sectionals.csv",
    "stewards_notes.csv",
    "stewards_stand_downs.csv",
    "dividends.csv",
    "tips.csv",
]

# Sheet name → CSV filename mapping for --sync-sheets
SHEETS_SYNC_MAP = {
    "harness_stride_profiles":    "stride_profiles.csv",
    "harness_stride_results":     "stride_results.csv",
    "harness_sectionals":         "sectionals.csv",
    "harness_stewards_notes":     "stewards_notes.csv",
    "harness_stewards_standdowns":"stewards_stand_downs.csv",
    "harness_dividends":          "dividends.csv",
    "harness_tips":               "tips.csv",
}


def sync_to_sheets(out_dir: str):
    """
    Write each sync CSV as a Google Sheet in the project Drive folder.
    Creates the sheet on first run, updates it in place on subsequent runs
    (sheet ID cached in sheet_ids.json so the project link stays stable).
    """
    gc = get_sheets_client()
    if not gc:
        return

    out_path   = Path(out_dir)
    cache_path = Path("sheet_ids.json")
    id_cache   = {}
    if cache_path.exists():
        try:
            id_cache = json.loads(cache_path.read_text())
        except Exception:
            pass

    print(f"\n📊 Syncing to Google Sheets — {len(SHEETS_SYNC_MAP)} sheets")

    for sheet_name, csv_file in SHEETS_SYNC_MAP.items():
        local_path = out_path / csv_file
        if not local_path.exists():
            print(f"  ⚠ {csv_file} not found locally — skipping")
            continue

        try:
            df = pd.read_csv(local_path, dtype=str).fillna("")
        except Exception as e:
            print(f"  ⚠ {csv_file} read error: {e}")
            continue

        # Cap at 50,000 rows to stay within Sheets limits (stride_results is large)
        if len(df) > 50000:
            df = df.head(50000)
            print(f"  ⚠ {csv_file} truncated to 50,000 rows for Sheets limit")

        rows = [df.columns.tolist()] + df.values.tolist()

        try:
            if sheet_name in id_cache:
                # Update existing sheet
                try:
                    sh = gc.open_by_key(id_cache[sheet_name])
                    ws = sh.sheet1
                    ws.clear()
                    ws.update(rows)
                    print(f"  ✅ {sheet_name} — updated ({len(df)} rows)")
                except Exception:
                    # Sheet was deleted — recreate
                    del id_cache[sheet_name]
                    raise

            if sheet_name not in id_cache:
                # Create new sheet in project folder
                sh = gc.create(sheet_name, folder_id=DRIVE_FOLDER_ID)
                ws = sh.sheet1
                ws.update(rows)
                id_cache[sheet_name] = sh.id
                print(f"  ✅ {sheet_name} — created ({len(df)} rows)")

        except Exception as e:
            print(f"  ✗ {sheet_name}: {e}")
            continue

        # Save cache after each sheet in case of interruption
        cache_path.write_text(json.dumps(id_cache, indent=2))

    print(f"\n   sheet_ids.json updated — sheet links are stable")


def get_drive_service():
    """Return an authenticated Google Drive v3 service using existing OAuth token."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("  ⚠ Run: pip install google-api-python-client google-auth-oauthlib")
        return None

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    token_path = Path("token.json")
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None  # force re-auth below

        if not creds:
            if not Path("credentials.json").exists():
                print("  ⚠ credentials.json not found.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", scopes)
            print("  → Opening browser for Google login...")
            # Force Chrome — Firefox mishandles the localhost redirect
            import webbrowser, os
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ]
            for cp in chrome_paths:
                if os.path.exists(cp):
                    webbrowser.register("chrome", None,
                                        webbrowser.BackgroundBrowser(cp))
                    os.environ["BROWSER"] = cp
                    break
            creds = flow.run_local_server(port=0)

        token_path.write_text(creds.to_json())
        print("  → Login saved to token.json")

    return build("drive", "v3", credentials=creds)


def sync_to_drive(out_dir: str, files: list = None):
    """
    Upload CSVs from out_dir to the project Drive folder.
    If a file with the same name already exists in the folder, it is replaced.
    files: list of filenames to sync (defaults to DRIVE_SYNC_FILES)
    """
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("  ⚠ Run: pip install google-api-python-client")
        return

    service = get_drive_service()
    if not service:
        return

    files_to_sync = files or DRIVE_SYNC_FILES
    out_path = Path(out_dir)

    print(f"\n☁️  Syncing to Google Drive folder: Project Harness Tas")

    # Build index of existing files in the folder
    existing = {}
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            existing[f["name"]] = f["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    for filename in files_to_sync:
        local_path = out_path / filename
        if not local_path.exists():
            print(f"  ⚠ {filename} — not found locally, skipping")
            continue

        media = MediaFileUpload(str(local_path), mimetype="text/csv", resumable=False)

        if filename in existing:
            # Update existing file (preserves file ID — Drive link stays stable)
            service.files().update(
                fileId=existing[filename],
                media_body=media,
            ).execute()
            print(f"  ✅ {filename} — updated")
        else:
            # Create new file in folder
            meta = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
            service.files().create(
                body=meta,
                media_body=media,
                fields="id",
            ).execute()
            print(f"  ✅ {filename} — created")

    print(f"   Done — {len(files_to_sync)} files synced")
    """Push horse data directly into Google Sheets in your Drive folder."""
    """Export horse data as a multi-tab Excel file."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    profile_raw = data.get("profile", {})
    name = _friendly_name(profile_raw, data["slug"])
    profile_clean = _clean_profile(profile_raw)

    # Profile
    ws = wb.create_sheet("Profile")
    _title(ws, f"🐴  {name}  —  Profile", 4)
    ws["A2"] = f"Source: {data['url']}  |  Scraped: {data['scraped_at']}"
    ws["A2"].font = META_FONT
    ws.merge_cells("A2:D2")
    _hdr(ws, 4, 1, "Field")
    _hdr(ws, 4, 2, "Value")
    for r, (k, v) in enumerate(profile_clean.items(), 5):
        ws.cell(r, 1, k).font = CELL_FONT
        ws.cell(r, 2, str(v)).font = CELL_FONT
        if r % 2 == 0:
            ws.cell(r, 1).fill = ALT_FILL
            ws.cell(r, 2).fill = ALT_FILL
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 42

    # Race Results
    ws2 = wb.create_sheet("Race Results")
    _title(ws2, f"Race Results  —  {name}  ({len(data.get('results', []))} runs)")
    _write_df(ws2, _clean_results(data.get("results", [])), 3)

    # Stats
    ws3 = wb.create_sheet("Stats Summary")
    _title(ws3, f"Stats  —  {name}", 4)
    stats = data.get("stats", {})
    row = 3
    if stats:
        for section_name, section_data in stats.items():
            ws3.cell(row, 1, section_name).font = Font(bold=True, name="Arial", size=11, color="1A3A5C")
            row += 1
            _hdr(ws3, row, 1, "Stat")
            _hdr(ws3, row, 2, "Value")
            row += 1
            for k, v in section_data.items():
                ws3.cell(row, 1, k.replace("_", " ").title()).font = CELL_FONT
                ws3.cell(row, 2, str(v)).font = CELL_FONT
                if row % 2 == 0:
                    ws3.cell(row, 1).fill = ALT_FILL
                    ws3.cell(row, 2).fill = ALT_FILL
                row += 1
            row += 1
    ws3.column_dimensions["A"].width = 28
    ws3.column_dimensions["B"].width = 20

    # Upcoming
    ws4 = wb.create_sheet("Upcoming Races")
    _title(ws4, f"Upcoming  —  {name}")
    upcoming = data.get("upcoming", [])
    if upcoming:
        _write_df(ws4, _clean_results(upcoming), 3)
    else:
        ws4.cell(3, 1, "No upcoming races scheduled.").font = META_FONT

    # Pedigree
    ws5 = wb.create_sheet("Pedigree")
    _title(ws5, f"Pedigree  —  {name}", 4)
    ped = data.get("pedigree", {})
    _hdr(ws5, 3, 1, "Relation")
    _hdr(ws5, 3, 2, "Horse / Name")
    for r, (k, v) in enumerate(ped.items(), 4):
        ws5.cell(r, 1, k).font = CELL_FONT
        ws5.cell(r, 2, str(v)).font = CELL_FONT
    if not ped:
        ws5.cell(4, 1, "No pedigree data found.").font = META_FONT
    ws5.column_dimensions["A"].width = 22
    ws5.column_dimensions["B"].width = 35

    # Debug
    ws6 = wb.create_sheet("Debug (API URLs)")
    _title(ws6, "API calls captured", 4)
    for r, u in enumerate(data.get("_api_urls", []), 3):
        ws6.cell(r, 1, u).font = CELL_FONT
    ws6.column_dimensions["A"].width = 110

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
        print(f"  ✅ Saved: {path}")
    except PermissionError:
        alt = path.replace(".xlsx", f"_{datetime.now().strftime('%H%M%S')}.xlsx")
        wb.save(alt)
        print(f"  ✅ File was open elsewhere — saved as: {alt}")


def export_person_sheets(data: dict, gc, sheet_id_cache: dict):
    """Push trainer/driver data directly into Google Sheets."""
    slug = data["slug"]
    entity = data.get("type", "person").title()
    profile = data.get("profile", {})
    name = _friendly_name(profile, slug)
    sheet_title = f"{name} - {entity} Data"
    folder_id = "1LVw4zyJSGGYhh6FOqYU4eRMVMFXRLUd9"
    cache_key = f"{entity.lower()}_{slug}"

    if cache_key in sheet_id_cache:
        try:
            spreadsheet = gc.open_by_key(sheet_id_cache[cache_key])
            print(f"  → Updating existing sheet: {sheet_title}")
        except Exception:
            spreadsheet = None
    else:
        spreadsheet = None

    if spreadsheet is None:
        spreadsheet = gc.create(sheet_title, folder_id=folder_id)
        sheet_id_cache[cache_key] = spreadsheet.id
        Path("sheet_ids.json").write_text(json.dumps(sheet_id_cache, indent=2))
        print(f"  → Created new sheet: {sheet_title}")
        print(f"     URL: https://docs.google.com/spreadsheets/d/{spreadsheet.id}")
        blank_sheet = spreadsheet.sheet1
    else:
        blank_sheet = None

    # Profile
    df_profile = pd.DataFrame(list(profile.items()), columns=["Field", "Value"])
    _write_sheet_tab(spreadsheet, "Profile", df_profile)

    # Results
    df_results = _clean_results(data.get("results", []))
    _write_sheet_tab(spreadsheet, "Race Results", df_results)

    # Stats
    stats = data.get("stats", {})
    if stats:
        stat_rows = []
        for section, vals in stats.items():
            for k, v in vals.items():
                stat_rows.append({"Section": section, "Stat": k.replace("_", " ").title(), "Value": v})
        _write_sheet_tab(spreadsheet, "Stats", pd.DataFrame(stat_rows))

    # Horses in stable (trainer only)
    horses = data.get("horses_in_stable", [])
    if horses:
        _write_sheet_tab(spreadsheet, "Horses in Stable", pd.DataFrame(horses))

    # Top horses by performance
    top_horses = data.get("top_horses", [])
    if top_horses:
        _write_sheet_tab(spreadsheet, "Top Horses",
                         pd.DataFrame([_flatten(h) if isinstance(h, dict) else h for h in top_horses]))

    if blank_sheet:
        try:
            spreadsheet.del_worksheet(blank_sheet)
        except Exception:
            pass

    sheet_id = sheet_id_cache[cache_key]
    print(f"  ✅ Google Sheet updated: https://docs.google.com/spreadsheets/d/{sheet_id}")
    return sheet_id


def export_person_csv(data: dict, out_dir: str):
    """Export trainer/driver data as CSV files."""
    slug = data["slug"]
    entity = data.get("type", "person")
    folder = Path(out_dir) / f"{entity}_{slug}"
    folder.mkdir(parents=True, exist_ok=True)

    profile = data.get("profile", {})
    if profile:
        pd.DataFrame(list(profile.items()), columns=["Field", "Value"]).to_csv(
            folder / "1_profile.csv", index=False)

    df_r = _clean_results(data.get("results", []))
    if not df_r.empty:
        df_r.to_csv(folder / "2_race_results.csv", index=False)

    horses = data.get("horses_in_stable", [])
    if horses:
        pd.DataFrame(horses).to_csv(folder / "3_horses_in_stable.csv", index=False)

    print(f"  ✅ CSV files saved to: {folder}")


def export_person(data: dict, path: str):
    """Export trainer/driver data as a multi-tab Excel file."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    entity = data.get("type", "person").title()
    name = data.get("profile", {}).get("Name", data["slug"].replace("-", " ").title())

    ws = wb.create_sheet("Profile")
    _title(ws, f"👤  {entity}: {name}", 4)
    _hdr(ws, 3, 1, "Field")
    _hdr(ws, 3, 2, "Value")
    for r, (k, v) in enumerate(data.get("profile", {}).items(), 4):
        ws.cell(r, 1, k).font = CELL_FONT
        ws.cell(r, 2, str(v)).font = CELL_FONT
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 42

    ws2 = wb.create_sheet("Race Results")
    _title(ws2, f"Race Results  —  {name}")
    _write_df(ws2, _clean_results(data.get("results", [])), 3)

    if data.get("horses_in_stable"):
        ws3 = wb.create_sheet("Horses in Stable")
        _title(ws3, f"Stable  —  {name}")
        _write_df(ws3, pd.DataFrame(data["horses_in_stable"]), 3)

    ws_dbg = wb.create_sheet("Debug (API URLs)")
    _title(ws_dbg, "API calls captured", 4)
    for r, u in enumerate(data.get("_api_urls", []), 3):
        ws_dbg.cell(r, 1, u).font = CELL_FONT
    ws_dbg.column_dimensions["A"].width = 110

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
        print(f"  ✅ Saved: {path}")
    except PermissionError:
        alt = path.replace(".xlsx", f"_{datetime.now().strftime('%H%M%S')}.xlsx")
        wb.save(alt)
        print(f"  ✅ File was open elsewhere — saved as: {alt}")


# ── Bulk mode ─────────────────────────────────────────────────────────────────

def scrape_bulk(txt_file: str, entity_type: str, out_dir: str):
    slugs = [s.strip() for s in Path(txt_file).read_text().splitlines()
             if s.strip() and not s.startswith("#")]
    print(f"\n📋 Bulk: {len(slugs)} {entity_type}(s)")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    all_results = []
    for slug in slugs:
        try:
            if entity_type == "horse":
                d = extract_horse_data(slug)
                export_horse_csv(d, out_dir)
                for row in d.get("results", []):
                    all_results.append({"_horse": slug, **row})
            else:
                d = extract_person_data(slug, entity_type)
                export_person(d, f"{out_dir}/{entity_type}_{slug}.xlsx")
        except Exception as e:
            print(f"  ⚠ Error scraping {slug}: {e}")
        time.sleep(DELAY)

    if all_results:
        csv_path = f"{out_dir}/_combined_results.csv"
        pd.DataFrame(all_results).to_csv(csv_path, index=False)
        print(f"\n✅ Combined CSV saved: {csv_path}")


# ── Meets ─────────────────────────────────────────────────────────────────────
# All Tassie harness meets for the 2025-26 season.
# Tuple: (track, race_date, upload_month_override)
# upload_month_override overrides the YYYY/MM folder on the upload server when the
# file sits in a different month than the race date (e.g. Laun Jan 31 → /2026/02/).

MEETS = [
    ("Hobart",      "2026-01-04", None),
    ("Scottsdale",  "2026-01-09", None),
    ("Hobart",      "2026-01-11", None),
    ("Hobart",      "2026-01-16", None),
    ("Burnie",      "2026-01-18", None),
    ("Carrick",     "2026-01-25", None),
    ("Launceston",  "2026-01-31", "2026/02"),
    ("Hobart",      "2026-02-01", None),
    ("Burnie",      "2026-02-06", None),
    ("Launceston",  "2026-02-08", None),
    ("Hobart",      "2026-02-13", None),
    ("Launceston",  "2026-02-15", None),
    ("Hobart",      "2026-02-20", None),
    ("Carrick",     "2026-02-22", None),
    ("Hobart",      "2026-02-28", "2026/03"),
    ("Burnie",      "2026-03-01", None),
    ("Launceston",  "2026-03-06", None),
    ("Carrick",     "2026-03-08", None),
    ("Burnie",      "2026-03-13", None),
    ("Hobart",      "2026-03-15", None),
    ("Hobart",      "2026-03-18", None),
    ("Launceston",  "2026-03-22", None),
    ("Hobart",      "2026-03-25", None),
]


def _upload_folder(date_str, month_override):
    """Return the YYYY/MM upload folder path, respecting month overrides."""
    if month_override:
        return month_override
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y/%m")


# ── Sectionals scraper ────────────────────────────────────────────────────────
# URL pattern: {BASE}/{YYYY}/{MM}/{Track}_{DDMMYYYY}.csv

SECTIONALS_BASE = "https://test.tasracing.com.au/wp-content/uploads"

# Meets whose filename doesn't follow the standard {Track}_{DDMMYYYY}.csv pattern
_SECT_OVERRIDES = {
    ("Carrick", "2026-02-22"): "Carrick_22022026-1.csv",
}


def _sectionals_url(track, date_str, month_override):
    folder   = _upload_folder(date_str, month_override)
    filename = _SECT_OVERRIDES.get(
        (track, date_str),
        f"{track}_{datetime.strptime(date_str, '%Y-%m-%d').strftime('%d%m%Y')}.csv",
    )
    return f"{SECTIONALS_BASE}/{folder}/{filename}"


def fetch_sectionals(out_dir):
    """Download and consolidate Tasracing sectionals CSVs into sectionals.csv."""
    import requests
    from io import StringIO

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    all_frames = []
    print(f"\n📐 Sectionals scrape — {len(MEETS)} meets")

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    for track, date_str, month_override in MEETS:
        url = _sectionals_url(track, date_str, month_override)
        print(f"  {track} {date_str}  →  {url.split('/')[-1]}")
        try:
            r = sess.get(url, timeout=20)
            if r.status_code == 200:
                df = pd.read_csv(StringIO(r.text))
                all_frames.append(df)
                print(f"     ✓ {len(df)} rows")
            else:
                print(f"     ⚠ HTTP {r.status_code}")
        except Exception as e:
            print(f"     ⚠ {e}")

    if not all_frames:
        print("\n  ⚠ No sectional data collected.")
        return

    combined  = pd.concat(all_frames, ignore_index=True)
    sort_cols = [c for c in ["Date", "Race No"] if c in combined.columns]
    if sort_cols:
        combined = combined.sort_values(sort_cols).reset_index(drop=True)

    out_file = out_path / "sectionals.csv"
    combined.to_csv(out_file, index=False)
    print(f"\n✅ sectionals.csv — {len(combined)} rows across {len(all_frames)}/{len(MEETS)} meets")


# ── Stewards scraper ──────────────────────────────────────────────────────────
# URL pattern: {BASE}/{YYYY}/{MM}/{YYYY-MM-DD}-{Club-Name}-Stewards-Report.pdf

STEWARDS_BASE = "https://test.tasracing.com.au/wp-content/uploads"

_STEWARDS_CLUBS = {
    "Hobart":      "Tasmanian-Trotting-Club",
    "Launceston":  "Launceston-Pacing-Club",
    "Burnie":      "Burnie-Harness-Racing-Club",
    "Scottsdale":  "North-Eastern-Pacing-Club",
    "Carrick":     "Carrick-Park-Pacing-Club",
    "St Marys":    "St-Marys-Pacing-Club",
}

# Meets uploaded with a wrong date in the filename: (track, race_date) -> actual filename
_STEWARDS_OVERRIDES = {
    # Burnie 2026-03-01 was uploaded as 2026-01-03 (transposed) but sits in /2026/03/ correctly
    ("Burnie",      "2026-03-01"): "2026-01-03-Burnie-Harness-Racing-Club-Stewards-Report.pdf",
    # Launceston 2026-02-08 was uploaded with plural "Reports" in filename
    ("Launceston",  "2026-02-08"): "2026-02-08-Launceston-Pacing-Club-Stewards-Reports.pdf",
    # Carrick 2026-01-25 was uploaded with -1 suffix (same quirk as sectionals CSV)
    ("Carrick",     "2026-01-25"): "2026-01-25-Carrick-Park-Pacing-Club-Stewards-Report-1.pdf",
}

# Meets that have a stewards report but are not in the main MEETS list
_STEWARDS_EXTRA = [
    ("St Marys", "2026-01-01", None),   # has stewards report, no sectionals CSV
]

_PENALTY_KEYS   = ("suspend", "fine", "reprimand", "caution", "disqualif",
                   "penalised", "penalized", "penalty")
_STANDDOWN_KEYS = ("stand down", "stand-down", "stood down", "not to start",
                   "vet stand", "veterinary")
_ACTION_KEYS    = ("out of draw", "scratched from draw", "last chance warning",
                   "last chance", " ood ")


def _parse_stewards_pdf(pdf_bytes, track, date_str):
    """
    Parse a Tasracing stewards report PDF into structured data.

    Extracts four things:
      race_notes    — per-horse narrative notes from each race section
      penalties     — suspensions, fines, reprimands (from summary table)
      stand_downs   — vet/stewards stand-downs (narrative + summary)
      horse_actions — out of draw, last chance warnings (from summary)

    Format is consistent across all Tassie venues:
      RACE N – RACE NAME – distanceM TYPE
      HORSE NAME – narrative note
      General – driver/inquiry notes (not horse-specific)
      ... summary table at end
    """
    try:
        import pdfplumber
    except ImportError:
        print("  ⚠ pdfplumber not installed — run: pip install pdfplumber")
        return {"race_notes": [], "penalties": [], "stand_downs": [], "horse_actions": []}

    import io

    race_notes    = []
    penalties     = []
    stand_downs   = []
    horse_actions = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    RACE_HDR = _re.compile(
        r'^RACE\s+(\d+)\s*[-–]\s*(.+?)\s*[-–]\s*(\d+)[Mm]?\s*([A-Z]{2,3})\s*$',
        _re.IGNORECASE
    )
    RACE_HDR2 = _re.compile(r'^RACE\s+(\d+)\s*[-–]\s*(.+)$', _re.IGNORECASE)
    HORSE_NOTE = _re.compile(r'^([A-Z][A-Z0-9\s\'\-\.]+?)\s*[-–]\s*(.+)$')
    # Format A: "HORSE NAME (Driver) note text" — older style without dash
    HORSE_NOTE_A = _re.compile(r'^([A-Z][A-Z0-9\s\'\-\.]{2,}?)\s*\([A-Z][A-Za-z\s\.]+\)\s+(.+)$')
    # "A warning placed on HORSE NAME which..." or "Warnings placed on HORSE, HORSE..."
    WARNING_LINE = _re.compile(r'^(?:A\s+)?[Ww]arnings?\s+placed\s+on\s+(.+)', _re.IGNORECASE)
    GENERAL    = _re.compile(r'^General\s*[-–]', _re.IGNORECASE)
    SUMMARY_START = _re.compile(r'RACE\s+MEETING\s+SUMMARY', _re.IGNORECASE)
    PENALTY_SECTION = _re.compile(
        r'^(SUSPENSIONS?|FINES?|REPRIMANDS?|CAUTIONS?)\s*[:\-]?\s*(.*)',
        _re.IGNORECASE
    )
    STANDDOWN_NARR  = _re.compile(r'stood\s+down', _re.IGNORECASE)
    STANDDOWN_TRIAL = _re.compile(
        r'stood\s+down\s+(?:pending\s+)?(?:the\s+completion\s+of\s+)?(\d+)\s+satisfactory\s+trials?',
        _re.IGNORECASE
    )
    HORSE_ACTION_ROW = _re.compile(
        r'^R?(\d+)[\.:]?\s+([A-Z][A-Z0-9\s\'\-\.]+?)\s*[-–\.]\s*(.+)$'
    )
    # Scratchings section entry: "R1. HORSE NAME. reason. Stood down N days."
    SCRATCH_ENTRY = _re.compile(
        r'R?(\d+)[\.:]?\s+([A-Z][A-Z0-9\s\'\-\.]+?)\.\s+(.+)',
    )

    NOT_HORSE = {
        'RACE', 'GENERAL', 'FINES', 'FINE', 'SUSPENSIONS', 'SUSPENSION',
        'REPRIMANDS', 'REPRIMAND', 'HORSE', 'ACTIONS', 'ACTION', 'PRE',
        'POST', 'SWABS', 'SWAB', 'FOLLOW', 'UPS', 'ADJOURNED', 'PENDING',
        'INQUIRIES', 'NIL', 'ALL', 'WINNERS', 'TCO2', 'BARRIER', 'INFORMATION',
        'SCRATCHINGS', 'LATE', 'NOTE', 'NOTES', 'CLUB', 'DATE', 'WEATHER',
        'TRACK', 'STEWARDS', 'PANEL', 'CHAIR', 'STARTER', 'VETERINARIAN',
        'MEDICAL', 'PROVIDER', 'CHECK', 'INSPECTIONS', 'DETAILS', 'AHR',
        'RULE', 'CAUTIONS', 'CAUTION',
    }

    def is_horse_name(text):
        text = text.strip()
        if not text or len(text) < 2:
            return False
        words = text.split()
        if all(w.upper() in NOT_HORSE for w in words):
            return False
        if any(w.islower() for w in words):
            return False
        if len(words) == 1 and len(words[0]) < 3:
            return False
        return True

    in_summary   = False
    current_race = None
    race_name    = ""
    penalty_type = None
    pending_ref  = ""
    pending_rule = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        if SUMMARY_START.search(line):
            in_summary = True
            i += 1
            continue

        if not in_summary:
            m = RACE_HDR.match(line)
            if m:
                current_race = int(m.group(1))
                race_name    = m.group(2).strip()
                i += 1
                continue

            m2 = RACE_HDR2.match(line)
            if m2 and line.upper().startswith("RACE "):
                try:
                    current_race = int(m2.group(1).split()[0])
                except ValueError:
                    pass
                race_name = m2.group(2).strip()
                i += 1
                continue

            if GENERAL.match(line):
                i += 1
                continue

            if current_race is not None:
                # Try Format B first: "HORSE NAME – note"
                m = HORSE_NOTE.match(line)
                horse_raw = note_text = None
                if m and is_horse_name(m.group(1).strip()):
                    horse_raw = m.group(1).strip()
                    note_text = m.group(2).strip()
                else:
                    # Try Format A: "HORSE NAME (Driver) note"
                    m2 = HORSE_NOTE_A.match(line)
                    if m2 and is_horse_name(m2.group(1).strip()):
                        horse_raw = m2.group(1).strip()
                        note_text = m2.group(2).strip()

                if horse_raw and note_text:
                    # Collect continuation lines
                    j = i + 1
                    while j < len(lines):
                        next_line = lines[j]
                        if (HORSE_NOTE.match(next_line) or
                                HORSE_NOTE_A.match(next_line) or
                                WARNING_LINE.match(next_line) or
                                GENERAL.match(next_line) or
                                next_line.upper().startswith("RACE ") or
                                SUMMARY_START.search(next_line) or
                                _re.match(r'^No racing incidents', next_line, _re.IGNORECASE)):
                            break
                        note_text += " " + next_line
                        j += 1
                    i = j

                    if STANDDOWN_NARR.search(note_text):
                        sd_m  = STANDDOWN_TRIAL.search(note_text)
                        dm    = _re.search(r'\b(\d+)\s+days?\b', note_text, _re.IGNORECASE)
                        days  = sd_m.group(1) if sd_m else (dm.group(1) if dm else "trial")
                        is_vet = bool(_re.search(
                            r'vet\s+examin|vet\s+stand|veterinary\s+advice', note_text, _re.IGNORECASE
                        ))
                        stand_downs.append({
                            "Date":       date_str,
                            "Track":      track,
                            "Race_No":    current_race,
                            "Horse":      horse_raw.title(),
                            "Type":       "Vet" if is_vet else "Stewards",
                            "Days":       days,
                            "Reason":     note_text.strip(),
                            "Horse_Slug": _name_to_slug(horse_raw),
                        })

                    race_notes.append({
                        "Date":       date_str,
                        "Track":      track,
                        "Race_No":    current_race,
                        "Race_Name":  race_name,
                        "Horse":      horse_raw.title(),
                        "Note":       note_text.strip(),
                        "Horse_Slug": _name_to_slug(horse_raw),
                    })
                    continue

                # "A warning placed on HORSE NAME which..." / "Warnings placed on H1, H2..."
                wm = WARNING_LINE.match(line)
                if wm:
                    # Collect full text including continuation
                    warn_text = line
                    j = i + 1
                    while j < len(lines):
                        nl = lines[j]
                        if (HORSE_NOTE.match(nl) or HORSE_NOTE_A.match(nl) or
                                WARNING_LINE.match(nl) or nl.upper().startswith("RACE ") or
                                SUMMARY_START.search(nl)):
                            break
                        warn_text += " " + nl
                        j += 1
                    i = j
                    # Extract horse names from warning text
                    # Pattern: "... on HORSE NAME (Driver) which" or "on H1, H2, H3 all of which"
                    horses_in_warning = _re.findall(
                        r'\b([A-Z][A-Z0-9\s\'\-]{2,}?)(?:\s*\([A-Z][A-Za-z\s\.]+\))?(?=\s+(?:which|who|all|,|\.))',
                        warn_text
                    )
                    for h in horses_in_warning:
                        h = h.strip()
                        if is_horse_name(h):
                            race_notes.append({
                                "Date":       date_str,
                                "Track":      track,
                                "Race_No":    current_race,
                                "Race_Name":  race_name,
                                "Horse":      h.title(),
                                "Note":       warn_text.strip(),
                                "Horse_Slug": _name_to_slug(h),
                            })
                    continue

            i += 1

        else:
            pm = PENALTY_SECTION.match(line)
            if pm:
                penalty_type = pm.group(1).upper().rstrip("S")
                rest = pm.group(2).strip()
                if rest and rest.upper() != "NIL":
                    lines.insert(i + 1, rest)
                i += 1
                continue

            if _re.match(r'^(PRE.RACE SWABS?|POST RACE|FOLLOW|ADJOURNED|NIL)', line, _re.IGNORECASE):
                penalty_type = None
                i += 1
                continue

            # BARRIER INFORMATION section → horse_actions
            if _re.match(r'^BARRIER\s+INFORMATION\s*[:\-]?', line, _re.IGNORECASE):
                penalty_type = "BARRIER_INFO"
                rest = _re.sub(r'^BARRIER\s+INFORMATION\s*[:\-]?\s*', '', line, flags=_re.IGNORECASE).strip()
                if rest:
                    lines.insert(i + 1, rest)
                i += 1
                continue

            # SCRATCHINGS section — contains stand-downs with day counts
            if _re.match(r'^SCRATCHINGS?\s*[:\-]?', line, _re.IGNORECASE):
                penalty_type = "SCRATCHING"
                rest = _re.sub(r'^SCRATCHINGS?\s*[:\-]?\s*', '', line, flags=_re.IGNORECASE).strip()
                if rest:
                    lines.insert(i + 1, rest)
                i += 1
                continue

            ha_match = _re.match(r'^HORSE\s+ACTIONS?\s*[:\-]?\s*(.*)', line, _re.IGNORECASE)
            if ha_match:
                penalty_type = "HORSE_ACTION"
                rest = ha_match.group(1).strip()
                if rest:
                    lines.insert(i + 1, rest)
                i += 1
                continue

            if penalty_type in ("SUSPENSION", "FINE", "REPRIMAND", "CAUTION"):
                if (not _re.match(r'^[A-Z]', line) and
                        not _re.match(r'^\d[\d/]*\s', line)):
                    i += 1
                    continue

                if _re.match(r'^\d[\d/]*\s+[\d()\w/\.]+$', line):
                    parts = line.split(None, 1)
                    pending_ref  = parts[0]
                    pending_rule = parts[1] if len(parts) > 1 else ""
                    i += 1
                    continue

                nm = _re.match(r'^(\d[\d/]*)\s+(.+)$', line)
                if nm:
                    ref_candidate = nm.group(1)
                    remainder     = nm.group(2)
                    dm = _re.split(r'\s*[-\u2013]\s*', remainder, maxsplit=1)
                    if len(dm) == 2:
                        person      = dm[0].strip()
                        detail_rule = dm[1].strip()
                        rm = _re.search(r'\s+([\d]+\([\w\/\.]+\))\s*$', detail_rule)
                        if rm:
                            detail = detail_rule[:rm.start()].strip()
                            rule   = rm.group(1)
                        else:
                            detail = detail_rule
                            rule   = pending_rule
                        penalties.append({
                            "Date":     date_str,
                            "Track":    track,
                            "Race_Ref": ref_candidate,
                            "Person":   person.title(),
                            "Type":     penalty_type,
                            "Detail":   detail,
                            "Rule":     rule,
                        })
                        pending_ref = pending_rule = ""
                        i += 1
                        continue

                m = _re.match(
                    r'^([A-Z][A-Za-z\s,\.]+?)\s*[-\u2013]\s*(.+?)(?:\s+([\d]+\([\w\/\.]+\)))?\s*$',
                    line
                )
                if m:
                    penalties.append({
                        "Date":     date_str,
                        "Track":    track,
                        "Race_Ref": pending_ref,
                        "Person":   m.group(1).strip().title(),
                        "Type":     penalty_type,
                        "Detail":   m.group(2).strip(),
                        "Rule":     m.group(3).strip() if m.group(3) else pending_rule,
                    })
                    pending_ref = pending_rule = ""

            elif penalty_type == "HORSE_ACTION":
                m = HORSE_ACTION_ROW.match(line)
                if m:
                    horse_raw = m.group(2).strip()
                    action    = m.group(3).strip()
                    action_lo = action.lower()

                    horse_actions.append({
                        "Date":       date_str,
                        "Track":      track,
                        "Race_No":    m.group(1).strip(),
                        "Horse":      horse_raw.title(),
                        "Action":     action,
                        "Horse_Slug": _name_to_slug(horse_raw),
                    })

                    is_standdown = ('stood down' in action_lo or 'trial' in action_lo
                                    or 'ods' in action_lo or 'odm' in action_lo
                                    or 'out of draw' in action_lo)
                    if is_standdown:
                        dm   = _re.search(r'\b(\d+)\s+days?\b', action, _re.IGNORECASE)
                        days = dm.group(1) if dm else "trial/draw"
                        stand_downs.append({
                            "Date":       date_str,
                            "Track":      track,
                            "Race_No":    m.group(1).strip(),
                            "Horse":      horse_raw.title(),
                            "Type":       "Stewards",
                            "Days":       days,
                            "Reason":     action,
                            "Horse_Slug": _name_to_slug(horse_raw),
                        })

            elif penalty_type == "BARRIER_INFO":
                # "R2. MILLYCENT. Out of draw mobile." format
                m = HORSE_ACTION_ROW.match(line)
                if m:
                    horse_raw = m.group(2).strip()
                    action    = m.group(3).strip()
                    if is_horse_name(horse_raw):
                        horse_actions.append({
                            "Date":       date_str,
                            "Track":      track,
                            "Race_No":    m.group(1).strip(),
                            "Horse":      horse_raw.title(),
                            "Action":     action,
                            "Horse_Slug": _name_to_slug(horse_raw),
                        })

            elif penalty_type == "SCRATCHING":
                # "R1. REID GRANT. Sore. Stood down 10 days."
                # Multiple entries may be on one line separated by horse names
                # Collect continuation lines first
                scratch_text = line
                j = i + 1
                while j < len(lines):
                    nl = lines[j]
                    if (_re.match(r'^(PRE.RACE|POST RACE|FOLLOW|ADJOURNED)', nl, _re.IGNORECASE) or
                            PENALTY_SECTION.match(nl) or
                            _re.match(r'^BARRIER', nl, _re.IGNORECASE)):
                        break
                    # Stop if next line starts a new R-number entry at the start
                    if _re.match(r'^R?\d+[\.:]', nl):
                        # Could be continuation of same scratchings block
                        scratch_text += " " + nl
                        j += 1
                        continue
                    scratch_text += " " + nl
                    j += 1
                i = j

                # Find each horse entry: "R1. HORSE NAME. reason" or "HORSE NAME. reason"
                entries = _re.findall(
                    r'R?(\d+)[\.:]?\s+([A-Z][A-Z0-9\s\'\-]+?)\.\s+([^R]+?)(?=R?\d+[\.:]|$)',
                    scratch_text + " "
                )
                for race_ref, horse_raw, reason in entries:
                    horse_raw = horse_raw.strip()
                    reason    = reason.strip()
                    if not is_horse_name(horse_raw) or not reason:
                        continue
                    dm   = _re.search(r'\b(\d+)\s+days?\b', reason, _re.IGNORECASE)
                    days = dm.group(1) if dm else "trial"
                    is_vet = bool(_re.search(
                        r'vet\s+|sore|virus|injur|lame|rash|ill', reason, _re.IGNORECASE
                    ))
                    if STANDDOWN_NARR.search(reason) or dm:
                        stand_downs.append({
                            "Date":       date_str,
                            "Track":      track,
                            "Race_No":    race_ref.strip(),
                            "Horse":      horse_raw.title(),
                            "Type":       "Vet" if is_vet else "Stewards",
                            "Days":       days,
                            "Reason":     reason,
                            "Horse_Slug": _name_to_slug(horse_raw),
                        })
                    horse_actions.append({
                        "Date":       date_str,
                        "Track":      track,
                        "Race_No":    race_ref.strip(),
                        "Horse":      horse_raw.title(),
                        "Action":     "Scratched - " + reason[:60],
                        "Horse_Slug": _name_to_slug(horse_raw),
                    })
                continue  # already advanced i

            i += 1

    seen_sd = set()
    deduped_sd = []
    for sd in stand_downs:
        key = sd["Horse"].lower()
        if key not in seen_sd:
            seen_sd.add(key)
            deduped_sd.append(sd)

    return {
        "race_notes":    race_notes,
        "penalties":     penalties,
        "stand_downs":   deduped_sd,
        "horse_actions": horse_actions,
    }


def fetch_stewards(out_dir):
    """Download and parse Tasracing stewards PDFs into structured CSVs."""
    import requests

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    all_notes         = []
    all_penalties     = []
    all_stand_downs   = []
    all_horse_actions = []

    all_meets = list(MEETS) + list(_STEWARDS_EXTRA)
    print(f"\n⚖️  Stewards scrape — {len(all_meets)} meets")

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    for track, date_str, month_override in all_meets:
        url = _stewards_url(track, date_str, month_override)
        print(f"  {track} {date_str}  →  {url.split('/')[-1]}")
        try:
            r = sess.get(url, timeout=30)
            if r.status_code == 200:
                parsed = _parse_stewards_pdf(r.content, track, date_str)
                all_notes.extend(parsed["race_notes"])
                all_penalties.extend(parsed["penalties"])
                all_stand_downs.extend(parsed["stand_downs"])
                all_horse_actions.extend(parsed["horse_actions"])
                n  = len(parsed["race_notes"])
                p  = len(parsed["penalties"])
                sd = len(parsed["stand_downs"])
                ha = len(parsed["horse_actions"])
                print(f"     ✓ {n} horse notes | {p} penalties | {sd} stand-downs | {ha} actions")
            else:
                print(f"     ⚠ HTTP {r.status_code}")
        except Exception as e:
            print(f"     ⚠ {e}")

    def _save_csv(rows, filename):
        if rows:
            df = (pd.DataFrame(rows)
                  .sort_values(
                      ["Date", "Track", "Race_No"]
                      if rows and "Race_No" in rows[0] else ["Date", "Track"]
                  )
                  .reset_index(drop=True))
            df.to_csv(out_path / filename, index=False)
            print(f"\n✅ {filename} — {len(df)} rows")
        else:
            print(f"\n  ⚠ {filename} — no data")

    _save_csv(all_notes,          "stewards_notes.csv")
    _save_csv(all_penalties,      "stewards_penalties.csv")
    _save_csv(all_stand_downs,    "stewards_stand_downs.csv")
    _save_csv(all_horse_actions,  "stewards_horse_actions.csv")


def _stewards_url(track, date_str, month_override):
    folder   = _upload_folder(date_str, month_override)
    club     = _STEWARDS_CLUBS.get(track, track.replace(" ", "-"))
    filename = _STEWARDS_OVERRIDES.get(
        (track, date_str),
        f"{date_str}-{club}-Stewards-Report.pdf",
    )
    return f"{STEWARDS_BASE}/{folder}/{filename}"


# ── Form guide scraper ───────────────────────────────────────────────────────

FORMGUIDE_BASE = "https://form.tasracing.com.au/formguide/Harness"
FORMGUIDE_MAX_RACES = 10


def _formguide_url(track: str, date_str: str, race_no: int) -> str:
    """Build the form guide racecard URL for a specific race."""
    d = date_str.replace("-", "")          # 20260109
    meet_id = f"H_{track}_{d}"
    return f"{FORMGUIDE_BASE}/{meet_id}/race/{meet_id}_{race_no}/racecard"


# Words that look like ALL-CAPS horse names but are actually page furniture
_HORSE_SKIP_WORDS = {
    'WIN', 'PLACE', 'TOTE', 'TRACK', 'RACE', 'BARRIER', 'FINISH', 'MARGIN',
    'DATE', 'DRIVER', 'TRAINER', 'WEIGHT', 'ODDS', 'SP', 'TAB', 'AGE', 'SEX',
    'FORM', 'NO', 'DNF', 'SCR', 'NR', 'SCRATCHINGS', 'SCRATCHING', 'PENALTY',
    'DNS', 'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'ACT', 'NT', 'GRADE',
    'CLASS', 'PACE', 'TROT', 'FIELD', 'PRIZE', 'MONEY', 'STARTS', 'LAST',
    'FIRST', 'SECOND', 'THIRD', 'RESULT', 'TIMES', 'MILES', 'YARDS', 'METERS',
}

_HORSE_NAME_RE = _re.compile(
    r'^([A-Z][A-Z0-9\'\-]*(?:\s+[A-Z0-9][A-Z0-9\'\-]*){0,5}(?:\s+\([A-Z]{2,3}\))?)$'
)


def _name_to_slug(name: str) -> str:
    """Convert a horse display name like 'GOODTIME OSCAR (NZ)' to a URL slug."""
    name = name.strip()
    # Extract trailing country suffix like (NZ), (GB)
    m = _re.search(r'\s*\(([A-Z]{2,3})\)\s*$', name)
    suffix = ''
    if m:
        suffix = '-' + m.group(1).lower()
        name = name[:m.start()].strip()
    slug = name.lower()
    slug = _re.sub(r"[^a-z0-9\s-]", '', slug)   # strip apostrophes etc.
    slug = _re.sub(r'\s+', '-', slug)
    slug = _re.sub(r'-+', '-', slug).strip('-')
    return slug + suffix


def _extract_horses_from_racecard(soup, lines: list) -> list:
    """
    Extract horse display names from a formguide racecard page.
    Returns a list of display name strings (not yet slugified).
    Tries three strategies in order, stopping at the first that yields results.
    """
    horses = []

    # Strategy 1: anchor tags whose href contains /horse/ (harness.au links)
    for a in soup.find_all('a', href=True):
        href = str(a.get('href', ''))
        text = a.get_text().strip()
        if text and _re.search(r'/horses?/', href, _re.IGNORECASE):
            if _HORSE_NAME_RE.match(text) and text not in _HORSE_SKIP_WORDS:
                horses.append(text)
    if horses:
        return list(dict.fromkeys(horses))

    # Strategy 2: table cells / leaf elements containing ALL-CAPS names
    for tag in soup.find_all(['td', 'span', 'div', 'p']):
        # Skip elements that are clearly containers (many block-level children)
        block_children = [
            c for c in tag.children
            if hasattr(c, 'name') and c.name not in (None, 'a', 'span', 'b',
                                                       'em', 'strong', 'i')
        ]
        if len(block_children) > 1:
            continue
        text = tag.get_text().strip()
        if (len(text) > 2 and _HORSE_NAME_RE.match(text)
                and text not in _HORSE_SKIP_WORDS):
            horses.append(text)
    if horses:
        return list(dict.fromkeys(horses))

    # Strategy 3: text lines matching "NN  HORSE NAME (NZ)" format
    prog_horse_re = _re.compile(
        r'^\d{1,2}\s{1,5}'
        r'([A-Z][A-Z0-9\'\-]*(?:\s+[A-Z0-9][A-Z0-9\'\-]*){0,5}'
        r'(?:\s+\([A-Z]{2,3}\))?)\s*$'
    )
    for line in lines:
        m = prog_horse_re.match(line)
        if m:
            name = m.group(1).strip()
            if name not in _HORSE_SKIP_WORDS:
                horses.append(name)
    return list(dict.fromkeys(horses))


def _scrape_racecard(page, url: str, track: str, date_str: str, race_no: int) -> dict:
    """
    Load a single racecard page and extract:
      - track condition (meet level, from first race only)
      - dividends (win, place, quinella, exacta, trifecta, first 4)
      - expert tips (tipster + 4 picks)
      - horse names (list of display names)
    Returns None if page is a 404 or has no race data.
    """
    try:
        resp = page.goto(url, timeout=45000, wait_until="networkidle")
        if resp and resp.status == 404:
            return None
    except Exception as e:
        print(f"    ⚠ Navigation: {e}")
        return None

    # Give JS time to render
    time.sleep(4)

    content = page.content()

    # Non-existent races render with a "Data Error" banner in the main content
    # area while still showing R1's header/tips — detect and bail immediately.
    if "data error" in content.lower():
        return None

    # Quick check — if no meaningful race content, bail
    if "racecard" not in content.lower() and "dividend" not in content.lower():
        # Try one more wait
        time.sleep(4)
        content = page.content()
        if "dividend" not in content.lower() and "margin" not in content.lower():
            return None

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, "html.parser")

    full_text = soup.get_text(separator="\n")
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    result = {
        "Date":     date_str,
        "Track":    track,
        "Race_No":  race_no,
    }

    # ── Track condition ──
    # Appears as "Track: Good" or "TRACK Good" near top of page
    for line in lines[:60]:
        m = _re.search(r"track\s*[:\-]?\s*(good|slow|heavy|fast|soft)", line, _re.IGNORECASE)
        if m:
            result["Track_Condition"] = m.group(1).title()
            break

    # ── Expert tips ──
    # Pattern: tipster name then "Top 4 tips: X-X-X-X"
    for i, line in enumerate(lines):
        m = _re.search(r"top\s*4\s*tips?\s*[:\-]?\s*([\d\-]+)", line, _re.IGNORECASE)
        if m:
            result["Tips_Picks"] = m.group(1).strip()
            # Tipster name usually on same line after picks, or nearby
            tipster_m = _re.search(
                r"top\s*4\s*tips?\s*[:\-]?\s*[\d\-]+\s+([A-Za-z][A-Za-z\s]+?)(?:\(|$)",
                line, _re.IGNORECASE
            )
            if tipster_m:
                result["Tips_Tipster"] = tipster_m.group(1).strip()
            else:
                # Check adjacent lines
                for adj in lines[max(0,i-2):i+3]:
                    if _re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", adj) and "tips" not in adj.lower():
                        result["Tips_Tipster"] = adj.strip()
                        break
            break

    # ── Dividends ──
    # The Result Summary table has no "Win"/"Place" labels — they appear in the
    # TOTE column as "$5.00 / $3.50" on the winner row.
    # Exotics (Quinella, Exacta, Trifecta, First Four) DO have label rows.
    div_map = {
        "Win":      None,
        "Place":    None,
        "Quinella": None,
        "Exacta":   None,
        "Trifecta": None,
        "First4":   None,
    }

    # Win + Place: first "$X.XX / $X.XX" pattern on the page (winner's TOTE row)
    for line in lines:
        m = _re.search(r'\$([\d,]+\.\d{2})\s*/\s*\$([\d,]+\.\d{2})', line)
        if m:
            div_map["Win"]   = m.group(1).replace(",", "")
            div_map["Place"] = m.group(2).replace(",", "")
            break
        # Win only — place shows as "-"
        if div_map["Win"] is None:
            m2 = _re.search(r'\$([\d,]+\.\d{2})\s*/\s*-', line)
            if m2:
                div_map["Win"] = m2.group(1).replace(",", "")

    # Exotics: find label row then grab the next dollar value within 6 lines
    exotic_kw = {
        "quinella":   "Quinella",
        "exacta":     "Exacta",
        "trifecta":   "Trifecta",
        "first four": "First4",
        "first 4":    "First4",
        "first4":     "First4",
        "f4":         "First4",
    }
    for i, line in enumerate(lines):
        lower = line.lower()
        for kw, col in exotic_kw.items():
            if kw in lower and div_map[col] is None:
                for j in range(i, min(i + 6, len(lines))):
                    m = _re.search(r'\$?\s*([\d,]+\.\d{2})\b', lines[j])
                    if m:
                        div_map[col] = m.group(1).replace(",", "")
                        break

    result.update({f"Div_{k}": v for k, v in div_map.items()})

    # ── Horse names ──
    result["Horses"] = _extract_horses_from_racecard(soup, lines)

    return result


def fetch_formguide(out_dir: str, visible: bool = False):
    """
    Scrape the Tasracing form guide for all meets in MEETS.
    Extracts dividends, expert tips, and track condition per race.
    Outputs: dividends.csv, tips.csv
    """
    from playwright.sync_api import sync_playwright

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    all_dividends = []
    all_tips      = []
    total_meets   = len(MEETS)

    print(f"\n🏇 Form guide scrape — {total_meets} meets, up to {FORMGUIDE_MAX_RACES} races each")
    print(f"   Estimated time: {total_meets * FORMGUIDE_MAX_RACES * 6 // 60}–"
          f"{total_meets * FORMGUIDE_MAX_RACES * 8 // 60} mins\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not visible,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()

        for meet_idx, (track, date_str, _override) in enumerate(MEETS, 1):
            print(f"  [{meet_idx}/{total_meets}] {track} {date_str}")
            meet_condition = None
            races_found    = 0

            for race_no in range(1, FORMGUIDE_MAX_RACES + 1):
                url = _formguide_url(track, date_str, race_no)
                data = _scrape_racecard(page, url, track, date_str, race_no)

                if data is None:
                    # No data — either 404 or no-race (e.g. abandoned race)
                    # Only stop iterating if we've already found races and
                    # hit 2 consecutive misses (handles gaps like the No Race R5)
                    if races_found > 0:
                        # Check one more race before giving up
                        next_url = _formguide_url(track, date_str, race_no + 1)
                        next_data = _scrape_racecard(page, next_url, track, date_str, race_no + 1)
                        if next_data is None:
                            print(f"    → Stopped at R{race_no} (2 consecutive misses)")
                            break
                        else:
                            # Gap race (abandoned/no race) — log and continue
                            print(f"    R{race_no} — no data (skipped)")
                            data = next_data
                            race_no += 1  # account for the extra fetch
                    elif race_no == 1:
                        # First race missing — meet page may not exist
                        print(f"    ⚠ No data for R1 — skipping meet")
                        break
                    continue

                races_found += 1

                # Capture track condition from first race of meet
                if meet_condition is None and data.get("Track_Condition"):
                    meet_condition = data["Track_Condition"]

                # Attach meet-level condition to all races
                data["Track_Condition"] = meet_condition

                # Split into dividends and tips rows
                div_row = {
                    "Date":            data["Date"],
                    "Track":           data["Track"],
                    "Race_No":         data["Race_No"],
                    "Track_Condition": data.get("Track_Condition", ""),
                    "Div_Win":         data.get("Div_Win", ""),
                    "Div_Place":       data.get("Div_Place", ""),
                    "Div_Quinella":    data.get("Div_Quinella", ""),
                    "Div_Exacta":      data.get("Div_Exacta", ""),
                    "Div_Trifecta":    data.get("Div_Trifecta", ""),
                    "Div_First4":      data.get("Div_First4", ""),
                }
                all_dividends.append(div_row)

                tips_row = {
                    "Date":        data["Date"],
                    "Track":       data["Track"],
                    "Race_No":     data["Race_No"],
                    "Tipster":     data.get("Tips_Tipster", ""),
                    "Picks":       data.get("Tips_Picks", ""),
                }
                all_tips.append(tips_row)

                won = data.get("Div_Win", "-") or "-"
                tips_str = data.get("Tips_Picks", "-") or "-"
                print(f"    R{race_no} ✓  Win: ${won:>8}  Tips: {tips_str}")

            time.sleep(1)  # brief pause between meets

        browser.close()

    # ── Write CSVs ──
    if all_dividends:
        df_d = pd.DataFrame(all_dividends)
        df_d = df_d.sort_values(["Date", "Track", "Race_No"]).reset_index(drop=True)
        df_d.to_csv(out_path / "dividends.csv", index=False)
        print(f"\n✅ dividends.csv — {len(df_d)} rows")
    else:
        print("\n  ⚠ No dividend data collected.")

    if all_tips:
        df_t = pd.DataFrame(all_tips)
        df_t = df_t.sort_values(["Date", "Track", "Race_No"]).reset_index(drop=True)
        df_t.to_csv(out_path / "tips.csv", index=False)
        print(f"✅ tips.csv — {len(df_t)} rows")
    else:
        print("  ⚠ No tips data collected.")

    print(f"\n   Tip: if dividend values look wrong, run with --visible to watch the browser")


# ── Horse discovery from form guide ──────────────────────────────────────────

def fetch_formguide_horses(horses_file: str, visible: bool = False):
    """
    Diff horses in sectionals.csv and horse_results.csv against horses_file,
    append any missing slugs. No browser needed.
    """
    from datetime import date as _date

    horses_path = Path(horses_file)
    if not horses_path.exists():
        print(f"  ⚠ File not found: {horses_file}")
        return

    existing_lines = horses_path.read_text(encoding="utf-8").splitlines()
    existing_slugs = {
        ln.strip() for ln in existing_lines
        if ln.strip() and not ln.strip().startswith("#")
    }
    print(f"\n🐴 Horse discovery — {len(existing_slugs)} slugs already in {horses_file}")

    found = {}  # slug → (name, source, context)

    def add_horse(name: str, source: str, context: str = ""):
        slug = _name_to_slug(name)
        if slug and len(slug) > 2 and slug not in found:
            found[slug] = (name.title(), source, context)

    # Source 1: sectionals.csv
    for candidate in [
        Path("output/claude_data/sectionals.csv"),
        Path("output/sectionals.csv"),
        Path("sectionals.csv"),
    ]:
        if candidate.exists():
            print(f"  Reading sectionals: {candidate}")
            df = pd.read_csv(candidate)
            if "Horse" in df.columns:
                for _, row in df.iterrows():
                    ctx = f"{row.get('Location','')} {row.get('Date','')} R{row.get('Race No','')}"
                    add_horse(str(row["Horse"]), "sectionals", ctx)
                print(f"    {len(df)} rows → {len(found)} unique horses so far")
            break

    # Source 2: horse_results.csv
    for candidate in [
        Path("output/claude_data/horse_results.csv"),
        Path("horse_results.csv"),
    ]:
        if candidate.exists():
            print(f"  Reading horse_results: {candidate}")
            df = pd.read_csv(candidate, usecols=["Horse"])
            before = len(found)
            for name in df["Horse"].dropna().unique():
                add_horse(str(name), "horse_results", "")
            print(f"    {len(df['Horse'].unique())} unique horses → "
                  f"{len(found) - before} new slugs added")
            break

    missing = {
        slug: info for slug, info in found.items()
        if slug not in existing_slugs
    }

    print(f"\n  Total unique horses found:  {len(found)}")
    print(f"  Already in file:            {len(existing_slugs)}")
    print(f"  New (missing from file):    {len(missing)}")

    if not missing:
        print("\n  ✅ All horses already in file — nothing to add")
        return

    print(f"\n  Adding {len(missing)} slugs to {horses_file}:")
    today = _date.today().isoformat()
    new_lines = [f"\n# Added from horse discovery {today}"]
    for slug, (display_name, source, context) in sorted(missing.items()):
        ctx_str = f" [{source}: {context}]" if context else f" [{source}]"
        print(f"    + {slug}  ({display_name}{ctx_str})")
        new_lines.append(slug)

    with open(horses_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(new_lines) + "\n")

    print(f"\n  ✅ {horses_file} updated — run --stride-horses --resume to scrape new horses")


def fetch_stride_meet_update(meet_date: str, out_dir: str):
    """
    Update stride data for all horses that ran on a specific meet date.
    Reads horse names from sectionals.csv, resolves to slugs, fetches
    only runs newer than each horse's last recorded date.
    Much faster than --update for the full list.
    """
    out_path = Path(out_dir)
    profiles_csv = out_path / "stride_profiles.csv"
    results_csv  = out_path / "stride_results.csv"

    # Find sectionals
    sectionals_path = None
    for candidate in [
        out_path / "sectionals.csv",
        Path("output/claude_data/sectionals.csv"),
        Path("sectionals.csv"),
    ]:
        if candidate.exists():
            sectionals_path = candidate
            break

    if not sectionals_path:
        print("  ⚠ sectionals.csv not found — run --sectionals first")
        return

    df_sec = pd.read_csv(sectionals_path)

    # Find horses from the target date — handle both date formats
    mask = (df_sec['Date'].astype(str).str.contains(
        meet_date.replace('-', '/').lstrip('0') if '-' in meet_date else meet_date,
        na=False
    )) | (df_sec['Date'].astype(str) == meet_date)

    # Try converting dates for comparison
    try:
        df_sec['_date_parsed'] = pd.to_datetime(df_sec['Date'], dayfirst=True, errors='coerce')
        target = pd.to_datetime(meet_date)
        mask = df_sec['_date_parsed'] == target
    except Exception:
        pass

    meet_horses = df_sec[mask]
    if meet_horses.empty:
        print(f"  ⚠ No horses found for date: {meet_date}")
        print(f"     Available dates: {sorted(df_sec['Date'].unique())[:5]} ...")
        return

    unique_horses = meet_horses['Horse'].dropna().unique()
    print(f"\n🏇 Stride update for {meet_date} — {len(unique_horses)} horses")

    # Build slug map from existing profiles
    slug_map = {}
    if profiles_csv.exists():
        df_p = pd.read_csv(profiles_csv, usecols=["Horse", "Slug"])
        for _, row in df_p.iterrows():
            slug_map[str(row["Horse"]).lower()] = (str(row["Horse"]), str(row["Slug"]))

    # Get last run date per slug from existing results
    last_run_date = {}
    if results_csv.exists():
        df_r = pd.read_csv(results_csv, usecols=["Slug", "Date"])
        for slug, grp in df_r.groupby("Slug"):
            last_run_date[slug] = grp["Date"].max()

    new_profiles = []
    new_runs     = []
    ok = skipped = 0
    total = len(unique_horses)

    for i, horse_name in enumerate(sorted(unique_horses), 1):
        # Resolve display name and slug
        name_key = horse_name.strip().lower()
        if name_key in slug_map:
            display, slug = slug_map[name_key]
        else:
            display = horse_name.strip().title()
            # Handle NZ suffix from sectionals ALL CAPS e.g. "HARRYS DEAL NZ"
            parts = display.split()
            if parts and parts[-1].upper() in ("NZ", "GB", "IRE"):
                slug = _name_to_slug(display.upper())
            else:
                slug = _name_to_slug(display)

        since = last_run_date.get(slug)

        try:
            profile, runs = _fetch_stride_horse(display, since_date=since)

            if not profile and not runs:
                # Try NZ variant
                display_nz = display + " NZ" if not display.endswith(" NZ") else display
                profile, runs = _fetch_stride_horse(display_nz, since_date=since)
                if profile or runs:
                    display = display_nz
                    slug = _name_to_slug(display.upper())

            if not profile and not runs:
                print(f"  [{i:>2}/{total}] {display:35s} ✗ no data")
                skipped += 1
                continue

            profile_row = _parse_stride_profile(display, slug, profile)
            run_rows    = _parse_stride_runs(display, slug, runs)
            new_profiles.append(profile_row)
            new_runs.extend(run_rows)

            new_str = f" +{len(run_rows)} new runs" if run_rows else " no new runs"
            print(f"  [{i:>2}/{total}] {display:35s}{new_str} | {profile_row.get('Trainer','')}")
            ok += 1

        except Exception as e:
            print(f"  [{i:>2}/{total}] {display:35s} ✗ ERROR: {e}")
            skipped += 1

        time.sleep(0.25)

    # Write output — update existing rows, append new ones
    if new_profiles and profiles_csv.exists():
        df_old_p = pd.read_csv(profiles_csv)
        updated_slugs = {r["Slug"] for r in new_profiles}
        df_old_p = df_old_p[~df_old_p["Slug"].isin(updated_slugs)]
        df_p = pd.concat([df_old_p, pd.DataFrame(new_profiles)], ignore_index=True)
        df_p.to_csv(profiles_csv, index=False)
        print(f"\n✅ stride_profiles.csv updated — {len(df_p)} horses")

    if new_runs and results_csv.exists():
        df_old_r = pd.read_csv(results_csv)
        df_new_r = pd.DataFrame(new_runs)
        df_r = pd.concat([df_old_r, df_new_r], ignore_index=True)
        df_r = df_r.drop_duplicates(subset=["Slug", "Race_Code"])
        df_r = df_r.sort_values(["Horse", "Date"], ascending=[True, False])
        df_r.to_csv(results_csv, index=False)
        print(f"✅ stride_results.csv updated — {len(df_r)} runs total (+{len(df_new_r)} new)")

    print(f"\n   {ok} fetched | {skipped} skipped")

# ── CLI ───────────────────────────────────────────────────────────────────────

# ── Stride API horse scraper ─────────────────────────────────────────────────

STRIDE_API    = "https://stride.racing-api.com"
STRIDE_CUTOFF = "2025-01-01"


def _stride_get(path: str, timeout: int = 8) -> dict:
    import requests
    url = f"{STRIDE_API}/{path}"
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json",
                "Referer": "https://form.tasracing.com.au/",
            })
            if r.status_code == 200:
                return r.json()
            return {}
        except Exception:
            if attempt == 0:
                time.sleep(1)
    return {}


def _build_slug_map(results_path: Path) -> dict:
    slug_map = {}
    if not results_path.exists():
        return slug_map
    try:
        df = pd.read_csv(results_path, usecols=["Horse"])
        for name in df["Horse"].dropna().unique():
            name = str(name).strip()
            slug = _name_to_slug(name)
            if slug and slug not in slug_map:
                slug_map[slug] = name
    except Exception:
        pass
    return slug_map


def _slug_to_display(slug: str, slug_map: dict) -> str:
    if slug in slug_map:
        return slug_map[slug]
    parts = slug.split("-")
    if parts and parts[-1].upper() in ("NZ", "GB", "IRE", "IR", "US", "FR", "DE"):
        return " ".join(p.title() for p in parts[:-1]) + " " + parts[-1].upper()
    return " ".join(p.title() for p in parts)


def _fetch_stride_horse(display_name: str, since_date: str = None) -> tuple:
    cutoff  = since_date or STRIDE_CUTOFF
    encoded = display_name.replace(" ", "%20")
    profile  = _stride_get(f"runner/harness/H_{encoded}")
    all_runs = []
    page     = 0
    while True:
        data = _stride_get(
            f"pastruns-with-interstate/runner/harness/H_{encoded}?page={page}"
        )
        runs = data.get("data", [])
        if not runs:
            break
        done = False
        for run in runs:
            if run.get("meetDate", "")[:10] <= cutoff:
                done = True
                break
            all_runs.append(run)
        if done or len(runs) < 10:
            break
        page += 1
        time.sleep(0.15)
    return profile, all_runs


def _parse_stride_profile(display_name: str, slug: str, profile: dict) -> dict:
    d = profile.get("details", {})
    row = {
        "Horse": display_name, "Slug": slug,
        "Trainer": d.get("trainerName", ""), "Owner": d.get("horseOwners", ""),
        "Breeder": d.get("breeder", ""), "Age": d.get("age", ""),
        "Sex": d.get("sex", ""), "Colour": d.get("colour", ""),
        "Sire": d.get("sire", ""), "Dam": d.get("dam", ""),
        "Last_Starts": d.get("lastStartsSummary", ""),
        "Last_Win_Date": (d.get("lastWinDate") or "")[:10],
        "Last_Win_Venue": d.get("lastWinVenueName", ""),
        "Start_Rating": d.get("startRating", ""),
        "Career_Starts": "", "Career_Wins": "", "Career_Seconds": "",
        "Career_Thirds": "", "Career_Win_Pct": "", "Career_Place_Pct": "",
        "Next_Race_Date": "", "Next_Race_Venue": "", "Next_Race_Name": "", "Next_Race_Dist": "",
    }
    for s in d.get("resultsSummaries", []):
        if s.get("name") == "TotalResults":
            row["Career_Starts"]   = s.get("starts", "")
            row["Career_Wins"]     = s.get("wins", "")
            row["Career_Seconds"]  = s.get("seconds", "")
            row["Career_Thirds"]   = s.get("thirds", "")
            w = s.get("winPercentage", "")
            p = s.get("placePercentage", "")
            row["Career_Win_Pct"]   = f"{w:.1%}" if isinstance(w, float) else ""
            row["Career_Place_Pct"] = f"{p:.1%}" if isinstance(p, float) else ""
    nr = d.get("nextRaceDetails") or {}
    if nr:
        row["Next_Race_Date"]  = (nr.get("meetDate") or "")[:10]
        row["Next_Race_Venue"] = nr.get("venueName", "")
        row["Next_Race_Name"]  = nr.get("raceName", "")
        row["Next_Race_Dist"]  = nr.get("raceDistance", "")
    for venue, stats in (d.get("resultsByVenue") or {}).items():
        if not venue:
            continue
        v = venue.title()
        s = stats.get("starts", 0)
        w = stats.get("wins", 0)
        row[f"{v}_Starts"] = s
        row[f"{v}_Wins"]   = w
        row[f"{v}_Places"] = stats.get("places", 0)
        row[f"{v}_Win_Pct"] = f"{w/s:.1%}" if s > 0 else "0.0%"
    return row


def _parse_stride_runs(display_name: str, slug: str, runs: list) -> list:
    rows = []
    for run in runs:
        results = run.get("results", [])
        winner  = next((r["horseName"].title() for r in results
                        if str(r.get("position")) == "1"), "")
        rows.append({
            "Horse": display_name, "Slug": slug,
            "Date":          (run.get("meetDate") or "")[:10],
            "Track":         run.get("venueName", ""),
            "Race_No":       run.get("raceNumber", ""),
            "Race_Code":     run.get("raceCode", ""),
            "Distance_m":    run.get("raceDistance", ""),
            "Class":         run.get("class", ""),
            "Starters":      run.get("starters", ""),
            "Barrier":       run.get("barrierPosition", ""),
            "Handicap":      run.get("handicapString", ""),
            "Tab_No":        run.get("tabNumber", ""),
            "Place":         run.get("position", ""),
            "Margin_m":      run.get("margin", ""),
            "SP":            run.get("startingPrice", ""),
            "Driver":        run.get("jockeyName", ""),
            "Trainer":       run.get("trainerName", ""),
            "Race_Time_s":   run.get("raceTime", ""),
            "Mile_Rate":     round(float(run["mileRate"]), 2) if run.get("mileRate") else "",
            "800_400_Time":  run.get("h800400Time", ""),
            "400_0_Time":    run.get("h400Time", ""),
            "800_Margin_m":  run.get("h800Margin", ""),
            "400_Margin_m":  run.get("h400Margin", ""),
            "800_Width":     run.get("h800Width", ""),
            "400_Width":     run.get("h400Width", ""),
            "Last_800m_Pos": run.get("last800MPosition", ""),
            "Speed_800_400": round(float(run["h800400Speed"]), 2) if run.get("h800400Speed") else "",
            "Speed_400_0":   round(float(run["h400Speed"]), 2) if run.get("h400Speed") else "",
            "Avg_Speed":     round(float(run["speed"]), 2) if run.get("speed") else "",
            "Winner":        winner,
        })
    return rows


def fetch_stride_horses(horses_file: str, out_dir: str,
                        resume: bool = False, update: bool = False):
    """
    Fetch Stride API profile + runs for all horses in horses_file.
    --resume: skip horses already in stride_profiles.csv
    --update: only fetch runs newer than last recorded date per horse
    """
    horses_path = Path(horses_file)
    if not horses_path.exists():
        print(f"  ⚠ File not found: {horses_file}")
        return

    slugs = [
        ln.strip() for ln in horses_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    profiles_csv = out_path / "stride_profiles.csv"
    results_csv  = out_path / "stride_results.csv"

    slug_map = {}
    for candidate in [
        out_path / "horse_results.csv",
        Path("output/claude_data/horse_results.csv"),
        Path("horse_results.csv"),
    ]:
        if candidate.exists():
            slug_map = _build_slug_map(candidate)
            print(f"  Slug map: {len(slug_map)} names from {candidate}")
            break

    existing_profile_slugs = set()
    last_run_date = {}

    if (resume or update) and profiles_csv.exists():
        df_ex = pd.read_csv(profiles_csv, usecols=["Slug"])
        existing_profile_slugs = set(df_ex["Slug"].dropna().unique())
        print(f"  Existing profiles: {len(existing_profile_slugs)} horses")

    if update and results_csv.exists():
        df_exr = pd.read_csv(results_csv, usecols=["Slug", "Date"])
        for slug, grp in df_exr.groupby("Slug"):
            last_run_date[slug] = grp["Date"].max()

    mode = "resume" if resume else ("update" if update else "full")
    print(f"\n🏇 Stride horse scrape [{mode}] — {len(slugs)} horses | cutoff: {STRIDE_CUTOFF}")
    print(f"   No browser — pure API\n")

    new_profiles = []
    new_runs     = []
    ok = skipped_resume = skipped_nodata = 0
    total = len(slugs)

    for i, slug in enumerate(slugs, 1):
        if resume and slug in existing_profile_slugs:
            skipped_resume += 1
            continue

        display = _slug_to_display(slug, slug_map)
        since   = last_run_date.get(slug) if update else None

        try:
            profile, runs = _fetch_stride_horse(display, since_date=since)

            if not profile and not runs:
                # Fallback 1: pure title case (handles "Dalton Shard Nz" vs "Dalton Shard NZ")
                display_fb = slug.replace("-", " ").title()
                if display_fb != display:
                    profile, runs = _fetch_stride_horse(display_fb, since_date=since)
                    if profile or runs:
                        display = display_fb

            if not profile and not runs:
                # Fallback 2: try NZ suffix variants (uppercase then title case)
                for suffix_variant in (" NZ", " Nz"):
                    base = _re.sub(r'\s+(NZ|Nz|nz)$', '', display).strip()
                    candidate = base + suffix_variant
                    if candidate != display:
                        profile, runs = _fetch_stride_horse(candidate, since_date=since)
                        if profile or runs:
                            display = candidate
                            slug = _name_to_slug(display.upper())
                            break

            if not profile and not runs:
                print(f"  [{i:>3}/{total}] {slug:45s} ✗ no data")
                skipped_nodata += 1
                continue

            profile_row = _parse_stride_profile(display, slug, profile)
            run_rows    = _parse_stride_runs(display, slug, runs)
            new_profiles.append(profile_row)
            new_runs.extend(run_rows)

            trainer = profile_row.get("Trainer", "")
            pages   = (len(runs) - 1) // 10 + 1 if runs else 0
            pg_str  = f" p{pages}" if pages > 1 else ""
            print(f"  [{i:>3}/{total}] {display:40s} {len(runs):>3} runs{pg_str} | {trainer}")
            ok += 1

        except Exception as e:
            print(f"  [{i:>3}/{total}] {slug:45s} ✗ ERROR: {e}")
            skipped_nodata += 1

        time.sleep(0.25)

    # Write output — append if resume, replace if update
    if new_profiles:
        if (resume or update) and profiles_csv.exists():
            df_old = pd.read_csv(profiles_csv)
            # Always remove existing rows for re-fetched slugs before appending
            df_old = df_old[~df_old["Slug"].isin({r["Slug"] for r in new_profiles})]
            df_p = pd.concat([df_old, pd.DataFrame(new_profiles)], ignore_index=True)
        else:
            df_p = pd.DataFrame(new_profiles)
        # Final dedup safety net — keep last occurrence per slug
        df_p = df_p.drop_duplicates(subset=["Slug"], keep="last")
        df_p.to_csv(profiles_csv, index=False)
        print(f"\n✅ stride_profiles.csv — {len(df_p)} horses")

    if new_runs:
        if (resume or update) and results_csv.exists():
            df_old_r = pd.read_csv(results_csv)
            df_new_r = pd.DataFrame(new_runs)
            df_r = pd.concat([df_old_r, df_new_r], ignore_index=True)
            df_r = df_r.drop_duplicates(subset=["Slug", "Race_Code"])
            df_r = df_r.sort_values(["Horse", "Date"], ascending=[True, False])
        else:
            df_r = pd.DataFrame(new_runs).sort_values(["Horse", "Date"], ascending=[True, False])
        df_r.to_csv(results_csv, index=False)
        print(f"✅ stride_results.csv — {len(df_r)} runs")

    print(f"\n   {ok} fetched | {skipped_resume} skipped (already done) | {skipped_nodata} skipped (no data)")



def main():
    ap = argparse.ArgumentParser(description="Harness Racing Scraper v2")
    ap.add_argument("--horse",      help="Horse slug, e.g. goodtime-oscar")
    ap.add_argument("--trainer",    help="Trainer slug")
    ap.add_argument("--driver",     help="Driver slug")
    ap.add_argument("--bulk",       help="Text file of slugs (one per line)")
    ap.add_argument("--type",       default="horse", help="Type for bulk: horse/trainer/driver")
    ap.add_argument("--out",        default="output", help="Output folder")
    ap.add_argument("--playwright", action="store_true", help="(always used, kept for compatibility)")
    ap.add_argument("--visible",    action="store_true",
                    help="Show browser window (useful for debugging blank results)")
    ap.add_argument("--csv", action="store_true",
                    help="Export as CSV files (for Google Sheets) instead of Excel")
    ap.add_argument("--sheets", action="store_true",
                    help="Push directly to Google Sheets (requires credentials.json)")
    ap.add_argument("--url", help="Debug: open any URL and print all API calls captured")
    ap.add_argument("--sectionals", action="store_true",
                    help="Download and consolidate Tasracing sectionals CSVs into sectionals.csv")
    ap.add_argument("--stewards", action="store_true",
                    help="Download and parse Tasracing stewards PDFs into structured CSVs")
    ap.add_argument("--formguide", action="store_true",
                    help="Scrape Tasracing form guide for dividends, tips, and track conditions")
    ap.add_argument("--find-horses", metavar="HORSES_FILE",
                    help="Diff horses in sectionals/results against HORSES_FILE, append missing slugs")
    ap.add_argument("--stride-horses", metavar="HORSES_FILE",
                    help="Fetch Stride API profiles + runs for all horses in HORSES_FILE")
    ap.add_argument("--update-meet", metavar="DATE",
                    help="Update stride data for all horses that ran on DATE (YYYY-MM-DD), "
                         "using sectionals.csv to identify runners")
    ap.add_argument("--sync-drive", action="store_true",
                    help="Upload current CSV outputs to Google Drive project folder")
    ap.add_argument("--sync-sheets", action="store_true",
                    help="Write current CSV outputs as Google Sheets in project Drive folder")
    ap.add_argument("--resume", action="store_true",
                    help="With --stride-horses: skip horses already in stride_profiles.csv")
    ap.add_argument("--update", action="store_true",
                    help="With --stride-horses: only fetch runs newer than last recorded date")

    args = ap.parse_args()

    headless = not args.visible

    # ── URL debug mode ──
    if args.url:
        from playwright.sync_api import sync_playwright
        print(f"\n🔍 Debug mode — loading: {args.url}")
        captured = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            def handle_route(route):
                try:
                    response = route.fetch()
                    ct = response.headers.get("content-type", "")
                    if response.status == 200 and "json" in ct:
                        captured.append(route.request.url)
                    route.fulfill(response=response)
                except Exception:
                    try:
                        route.abort()
                    except Exception:
                        pass
            page.route("**/*", handle_route)
            try:
                page.goto(args.url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(3)
            except Exception as e:
                print(f"  ⚠ Page load note: {e}")
            browser.close()
        print(f"\n📋 {len(captured)} JSON responses captured:")
        for u in captured:
            print(f"   {u}")
        return

    # Load sheet ID cache so we update existing sheets instead of creating new ones
    sheet_id_cache = {}
    cache_path = Path("sheet_ids.json")
    if cache_path.exists():
        try:
            sheet_id_cache = json.loads(cache_path.read_text())
        except Exception:
            pass

    gc = None
    if args.sheets:
        gc = get_sheets_client()
        if not gc:
            print("  ⚠ Falling back to CSV export.")
            args.csv = True
            args.sheets = False

    if args.horse:
        d = extract_horse_data(args.horse, headless)
        if args.sheets and gc:
            export_horse_sheets(d, gc, sheet_id_cache)
        elif args.csv:
            export_horse_csv(d, args.out)
        else:
            export_horse_csv(d, args.out)

    if args.trainer:
        d = extract_person_data(args.trainer, "trainer", headless)
        if args.sheets and gc:
            export_person_sheets(d, gc, sheet_id_cache)
        elif args.csv:
            export_person_csv(d, args.out)
        else:
            export_person(d, f"{args.out}/trainer_{args.trainer}.xlsx")

    if args.driver:
        d = extract_person_data(args.driver, "driver", headless)
        if args.sheets and gc:
            export_person_sheets(d, gc, sheet_id_cache)
        elif args.csv:
            export_person_csv(d, args.out)
        else:
            export_person(d, f"{args.out}/driver_{args.driver}.xlsx")

    if args.bulk:
        scrape_bulk(args.bulk, args.type, args.out)

    if args.sectionals:
        fetch_sectionals(args.out)

    if args.stewards:
        fetch_stewards(args.out)

    if args.formguide:
        fetch_formguide(args.out, visible=not headless)

    if args.find_horses:
        fetch_formguide_horses(args.find_horses, visible=not headless)

    if args.stride_horses:
        fetch_stride_horses(args.stride_horses, args.out,
                            resume=args.resume, update=args.update)

    if args.update_meet:
        fetch_stride_meet_update(args.update_meet, args.out)

    if args.sync_drive:
        sync_to_drive(args.out)

    if args.sync_sheets:
        sync_to_sheets(args.out)

    if not any([args.horse, args.trainer, args.driver, args.bulk,
                args.url, args.sectionals, args.stewards, args.formguide,
                args.find_horses, args.stride_horses, args.update_meet,
                args.sync_drive, args.sync_sheets]):
        ap.print_help()

    close_browser_session()


if __name__ == "__main__":
    main()

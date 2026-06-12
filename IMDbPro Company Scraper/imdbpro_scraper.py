#!/usr/bin/env python3
"""
IMDbPro Company Scraper v2 — SPEED MODE for 6,000+ URLs
========================================================
Architecture:
  - N async agents (default 6) with INDEPENDENT timers (no shared lock bottleneck)
  - Per-agent ~3.5s delay between requests
  - NO wave batching, NO cooldowns — all URLs in one queue
  - 1 retry max, 20s per-request timeout — skip failures fast
  - Atomic, self-healing checkpoint (survives a mid-write crash)
  - Checkpoint/resume: crash-safe restart
  - HTML cache: raw pages saved to disk
  - Incremental CSV: results written live
  - Adaptive throttle: agents independently speed up, global brake on 429s

Usage:
    python imdbpro_scraper.py                    # first run
    python imdbpro_scraper.py --resume           # resume after crash
    python imdbpro_scraper.py --reparse          # re-parse cached HTML (no network)
    python imdbpro_scraper.py --input urls.csv   # custom input file

Expected runtime: ~35-50 min for 6,000 URLs (speed mode)
"""

import asyncio
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse

try:
    import aiohttp
    from bs4 import BeautifulSoup
except ImportError:
    print("[SETUP] Installing dependencies...")
    os.system("pip install aiohttp beautifulsoup4 lxml aiofiles --break-system-packages -q")
    import aiohttp
    from bs4 import BeautifulSoup

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

NUM_AGENTS         = 6        # concurrent workers (each with own rotating session)
WAVE_SIZE          = 99999    # effectively no waves
WAVE_COOLDOWN      = 0        # no cooldowns
INITIAL_DELAY      = 3.5      # delay between requests PER AGENT
MIN_DELAY          = 1.5      # fastest we'll ever go
MAX_DELAY          = 15       # slowest on backoff
MAX_RETRIES        = 1        # 1 retry max — skip failures fast
BACKOFF_BASE       = 10       # light backoff on 429
MAX_BACKOFF        = 30       # hard cap on any single 429 sleep (seconds)
SESSION_ROTATE_EVERY = 50     # rotate UA every N requests per agent
SESSION_REFRESH_EVERY = 7    # create fresh TCP session every N URLs (anti-tarpit)
CHECKPOINT_EVERY   = 10       # save checkpoint every N completed URLs
REQUEST_TIMEOUT_TOTAL = 20    # per-request hard ceiling (seconds)
REQUEST_SOCK_READ     = 12    # per-request read-stall ceiling (seconds)
# Outer per-URL watchdog. MUST comfortably exceed the worst-case in-fetch time:
# (MAX_RETRIES+1) attempts × REQUEST_TIMEOUT_TOTAL, plus a possible 429 backoff
# (up to MAX_BACKOFF) and the adaptive inter-request delay. Set too tight and
# every legitimate 429 backoff gets mis-cancelled and recorded as a "tarpit".
OUTER_TIMEOUT      = 120      # seconds — watchdog around a single URL
HTML_CACHE_DIR     = "html_cache"
CHECKPOINT_FILE    = "checkpoint.json"
OUTPUT_CSV         = "imdbpro_results.csv"
OUTPUT_JSON        = "imdbpro_results.json"
LOG_FILE           = "scraper.log"
COOKIE_FILE        = "cookies.txt"  # Netscape format

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-5s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("imdbpro")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CompanyResult:
    url: str
    company_name: str = ""
    category: str = ""
    companymeter: str = ""
    website: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = ""
    location_raw: str = ""
    staff_count: int = 0
    client_count: int = 0
    status: str = "pending"
    error_msg: str = ""
    agent_id: int = 0
    scraped_at: str = ""
    http_status: int = 0


# Canonical CSV column order — single source of truth for every CSV writer.
CSV_FIELDS = [
    "company_name", "category", "companymeter", "website", "phone", "email",
    "address", "city", "state", "zip_code", "country", "location_raw",
    "staff_count", "client_count", "url", "status", "error_msg",
    "http_status", "agent_id", "scraped_at",
]


# ═══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE RATE LIMITER (shared across all agents)
# ═══════════════════════════════════════════════════════════════════════════════

class AdaptiveThrottle:
    """
    Per-agent independent timing with global 429 brake.
    Each agent tracks its own last_request_time — no shared lock for sleeping.
    If 429s spike, ALL agents slow down via shared delay.
    """
    def __init__(self):
        self.current_delay = INITIAL_DELAY
        self.lock = asyncio.Lock()  # only for updating shared state, NOT for sleeping
        self.consecutive_ok = 0
        self.consecutive_fail = 0
        self.total_429s = 0
        self._agent_timers: Dict[int, float] = {}  # per-agent last request time

    async def wait(self, agent_id: int):
        """Each agent sleeps independently based on its own timer."""
        now = time.time()
        last = self._agent_timers.get(agent_id, 0)
        jitter = random.uniform(-0.5, 1.0)
        wait_time = max(0, self.current_delay + jitter)
        elapsed = now - last
        if elapsed < wait_time:
            await asyncio.sleep(wait_time - elapsed)
        self._agent_timers[agent_id] = time.time()

    async def report_success(self):
        async with self.lock:
            self.consecutive_ok += 1
            self.consecutive_fail = 0
            if self.consecutive_ok >= 50 and self.current_delay > MIN_DELAY:
                self.current_delay = max(MIN_DELAY, self.current_delay - 0.5)
                self.consecutive_ok = 0
                log.info(f"⚡ Throttle speeding up → {self.current_delay:.1f}s")

    async def report_rate_limit(self):
        async with self.lock:
            self.total_429s += 1
            self.consecutive_fail += 1
            self.consecutive_ok = 0
            self.current_delay = min(MAX_DELAY, self.current_delay * 1.5)
            log.warning(f"🛑 429 #{self.total_429s} — ALL agents → {self.current_delay:.1f}s")

    async def report_server_error(self):
        async with self.lock:
            self.consecutive_fail += 1
            self.consecutive_ok = 0
            self.current_delay = min(MAX_DELAY, self.current_delay * 1.3)

    @property
    def stats(self):
        return f"delay={self.current_delay:.1f}s, 429s={self.total_429s}"


# ═══════════════════════════════════════════════════════════════════════════════
# COOKIE PARSER (Netscape format)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_netscape_cookies(filepath: str) -> Dict[str, str]:
    """Parse Netscape cookie file → extract only IMDb/IMDbPro cookies."""
    cookies = {}
    try:
        with open(filepath, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain = parts[0]
                    name = parts[5]
                    value = parts[6]
                    if "imdb.com" in domain.lower():
                        cookies[name] = value
    except OSError as e:
        log.error(f"❌ Could not read cookie file {filepath}: {e}")
        return {}

    critical = ["session-token", "at-main", "ubid-main", "session-id"]
    found = [k for k in critical if k in cookies]
    missing = [k for k in critical if k not in cookies]

    log.info(f"🍪 Loaded {len(cookies)} IMDb cookies (critical: {len(found)}/{len(critical)})")
    if missing:
        log.warning(f"⚠️  Missing cookies: {missing}")
    if "at-main" not in cookies:
        log.error("❌ Missing 'at-main' — auth token. Will likely get redirected to login.")

    return cookies


# ═══════════════════════════════════════════════════════════════════════════════
# URL LOADER (handles CSV with any column layout)
# ═══════════════════════════════════════════════════════════════════════════════

def load_urls(filepath: str) -> List[str]:
    """Load URLs from CSV or TXT. Auto-detects URL column."""
    urls = []

    if filepath.endswith(".csv"):
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)

            # Find URL column by scanning header
            url_col = None
            if header:
                for i, col in enumerate(header):
                    col_lower = col.lower().strip()
                    if any(kw in col_lower for kw in ["url", "link", "imdb", "href", "website", "company_url"]):
                        url_col = i
                        break
                # Header might itself be a URL
                if url_col is None:
                    for i, col in enumerate(header):
                        if "imdb.com" in str(col):
                            url_col = i
                            urls.append(col.strip())
                            break
                if url_col is None:
                    url_col = 0
                    if header[0] and "imdb.com" in header[0]:
                        urls.append(header[0].strip())

            for row in reader:
                if url_col is not None and url_col < len(row):
                    val = row[url_col].strip()
                    if val and "imdb.com" in val:
                        urls.append(val)
                else:
                    for cell in row:
                        cell = cell.strip()
                        if "imdb.com" in cell and "/company/" in cell:
                            urls.append(cell)
                            break
    else:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "imdb.com" in line:
                    urls.append(line)

    # Normalize + deduplicate
    normalized = []
    seen = set()
    for url in urls:
        try:
            url = url.strip()
            if not url:
                continue
            if not url.startswith("http"):
                url = "https://" + url.lstrip("/")
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            if "imdb.com" not in netloc:
                continue
            path = (parsed.path.rstrip("/") or "") + "/"
            # Company pages live on pro.imdb.com — normalize any imdb host to it.
            if path.startswith("/company") and netloc != "pro.imdb.com":
                netloc = "pro.imdb.com"
            clean = f"{parsed.scheme.lower()}://{netloc}{path}"
            if clean not in seen:
                seen.add(clean)
                normalized.append(clean)
        except (ValueError, AttributeError):
            continue

    log.info(f"📋 Loaded {len(normalized)} unique company URLs (from {len(urls)} raw)")
    return normalized


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKPOINT / RESUME
# ═══════════════════════════════════════════════════════════════════════════════

class Checkpoint:
    def __init__(self, filepath: str = CHECKPOINT_FILE):
        self.filepath = filepath
        self.completed: Dict[str, dict] = {}
        self.lock = asyncio.Lock()
        self._load()

    def _load(self):
        for path in (self.filepath, self.filepath + ".bak"):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.completed = data.get("completed", {}) or {}
                if path != self.filepath:
                    log.warning(f"📁 Primary checkpoint unreadable — recovered from {path}")
                log.info(f"📁 Checkpoint loaded: {len(self.completed)} URLs already done")
                return
            except (json.JSONDecodeError, OSError, ValueError) as e:
                log.warning(f"⚠️  Checkpoint {path} unreadable ({e}); trying backup…")
        self.completed = {}

    def _write(self):
        """Atomic checkpoint write: temp file → fsync → rotate .bak → replace.

        Never leaves a half-written checkpoint behind, so a crash mid-save is
        always recoverable from either the primary file or the .bak.
        """
        payload = {
            "completed": self.completed,
            "saved_at": datetime.now().isoformat(),
            "count": len(self.completed),
        }
        tmp = f"{self.filepath}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(self.filepath):
            try:
                os.replace(self.filepath, self.filepath + ".bak")
            except OSError:
                pass
        os.replace(tmp, self.filepath)

    async def save(self):
        async with self.lock:
            try:
                self._write()
            except OSError as e:
                log.error(f"⚠️  Checkpoint save failed: {e}")

    async def mark_done(self, url: str, result: dict):
        async with self.lock:
            self.completed[url] = result
            # NOTE: call _write() directly, NOT await self.save(). asyncio.Lock is
            # not reentrant, so re-acquiring self.lock from inside the lock would
            # deadlock the worker until the watchdog kills it.
            if len(self.completed) % CHECKPOINT_EVERY == 0:
                try:
                    self._write()
                except OSError as e:
                    log.error(f"⚠️  Checkpoint save failed: {e}")

    def is_done(self, url: str) -> bool:
        return url in self.completed

    def get_remaining(self, all_urls: List[str]) -> List[str]:
        return [u for u in all_urls if u not in self.completed]


# ═══════════════════════════════════════════════════════════════════════════════
# HTML CACHE (re-parse without re-scraping)
# ═══════════════════════════════════════════════════════════════════════════════

class HtmlCache:
    def __init__(self, cache_dir: str = HTML_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    def _key(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def get(self, url: str) -> Optional[str]:
        path = self.cache_dir / f"{self._key(url)}.html"
        try:
            if path.exists() and path.stat().st_size > 0:
                return path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            log.warning(f"⚠️  Cache read failed for {url[:60]}: {e}")
        return None

    def put(self, url: str, html: str):
        if not html:
            return
        key = self._key(url)
        path = self.cache_dir / f"{key}.html"
        tmp = self.cache_dir / f"{key}.html.tmp"
        try:
            tmp.write_text(html, encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            log.warning(f"⚠️  Cache write failed for {url[:60]}: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    @property
    def size(self) -> int:
        return len(list(self.cache_dir.glob("*.html")))


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_company_page(html: str, url: str) -> CompanyResult:
    """Parse IMDbPro company page — extract data from __NEXT_DATA__ JSON."""
    result = CompanyResult(url=url, scraped_at=datetime.now().isoformat())

    # ── Auth / Error detection ──
    if "sign in" in html[:2000].lower() and "company" not in html[:2000].lower():
        result.status = "auth_expired"
        result.error_msg = "Redirected to login"
        return result
    if "page not found" in html[:2000].lower():
        result.status = "not_found"
        result.error_msg = "404"
        return result

    # ── Extract __NEXT_DATA__ JSON ──
    # Format: <script id="__NEXT_DATA__" type="application/json">{...}</script>
    # Anchor on the actual <script id="__NEXT_DATA__" ...> tag so we don't trip
    # over the literal string "__NEXT_DATA__" appearing elsewhere in the page.
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>', html)
    if m:
        gt_idx = m.end() - 1            # position of the '>' that opens content
    else:
        nd_idx = html.find('__NEXT_DATA__')
        if nd_idx == -1:
            result.status = "error"
            result.error_msg = "No __NEXT_DATA__ found"
            return result
        gt_idx = html.find('>', nd_idx)
        if gt_idx == -1:
            result.status = "error"
            result.error_msg = "Malformed __NEXT_DATA__ tag"
            return result
    # Find </script> that closes it
    close_idx = html.find('</script>', gt_idx)
    if close_idx == -1:
        result.status = "error"
        result.error_msg = "Unclosed __NEXT_DATA__ script"
        return result
    json_str = html[gt_idx + 1:close_idx].strip()
    if not json_str:
        result.status = "error"
        result.error_msg = "Empty __NEXT_DATA__ script"
        return result

    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as e:
        result.status = "error"
        result.error_msg = f"JSON parse error: {e}"
        return result

    # Navigate to company data — every hop may be missing OR explicitly null.
    try:
        page_props = (data or {}).get("props", {}).get("pageProps", {}) or {}
        company = (page_props.get("data") or {}).get("company")
    except (KeyError, TypeError, AttributeError):
        page_props, company = {}, None
    if not isinstance(company, dict):
        result.status = "error"
        result.error_msg = "Missing company data in JSON"
        return result

    # ── Company Name ──
    result.company_name = page_props.get("companyName") or ""
    if not result.company_name:
        try:
            result.company_name = company["companyText"]["text"]
        except (KeyError, TypeError):
            pass

    # ── Category ──
    try:
        types = company.get("companyTypes", [])
        if types:
            result.category = ", ".join(t["text"] for t in types if t.get("text"))
    except (KeyError, TypeError):
        pass

    # ── COMPANYmeter ──
    try:
        result.companymeter = str(company["meterRank"]["currentRank"])
    except (KeyError, TypeError):
        pass

    # ── Country (top-level) ──
    try:
        country_id = company["country"]["id"]
        country_map = {"US": "USA", "GB": "UK", "CA": "Canada", "AU": "Australia",
                       "DE": "Germany", "FR": "France", "IN": "India", "JP": "Japan",
                       "KR": "South Korea", "IT": "Italy", "ES": "Spain", "BR": "Brazil",
                       "MX": "Mexico", "NZ": "New Zealand", "IE": "Ireland", "SE": "Sweden",
                       "NO": "Norway", "DK": "Denmark", "NL": "Netherlands", "IL": "Israel"}
        result.country = country_map.get(country_id, country_id)
    except (KeyError, TypeError):
        pass

    # ── Staff / Client Counts ──
    try:
        result.staff_count = company["keyStaff"]["recordPoolSize"]
    except (KeyError, TypeError):
        pass
    try:
        result.client_count = company["knownForClients"]["recordPoolSize"]
    except (KeyError, TypeError):
        pass

    # ── Contact Info from Branches ──
    branches = []
    try:
        branches = company.get("branches", {}).get("edges", [])
    except (KeyError, TypeError, AttributeError):
        pass

    if branches:
        try:
            branch = branches[0]["node"]
        except (KeyError, TypeError, IndexError):
            branch = {}

        contact = {}
        try:
            contact = branch.get("directContact") or {}
        except (AttributeError):
            pass

        branch_name = ""
        try:
            branch_name = branch["name"]["value"]
        except (KeyError, TypeError):
            pass

        # Website
        try:
            ws = contact.get("website") or {}
            result.website = ws.get("url") or ws.get("label") or ""
        except (KeyError, TypeError, AttributeError):
            pass

        # Phone
        try:
            phones = contact.get("phoneNumbers") or []
            if phones and phones[0].get("value"):
                raw = phones[0]["value"]
                if raw.isdigit() and len(raw) == 10:
                    result.phone = f"({raw[:3]}) {raw[3:6]}-{raw[6:]}"
                else:
                    result.phone = raw
        except (KeyError, TypeError, IndexError, AttributeError):
            pass

        # Email
        try:
            email_val = contact.get("emailAddress")
            if email_val and email_val != "None" and isinstance(email_val, str):
                result.email = email_val
        except (KeyError, TypeError, AttributeError):
            pass

        # Address
        try:
            phys = contact.get("physicalAddress") or {}
            addr_text = phys.get("text") or ""
            if addr_text:
                result.address = addr_text
                result.location_raw = addr_text
                _parse_address(result, addr_text, branch_name)
        except (KeyError, TypeError, AttributeError):
            pass

        # Fallback: use branch name for city/state if no address parsed
        if not result.city and branch_name:
            csz = re.match(r'([^,]+),\s*([A-Z]{2})', branch_name)
            if csz:
                result.city = csz.group(1).strip()
                result.state = csz.group(2).strip()
                if not result.country:
                    result.country = "USA"

        # If multiple branches, collect all locations
        if len(branches) > 1:
            all_locs = []
            for edge in branches:
                try:
                    bname = edge["node"]["name"]["value"]
                    all_locs.append(bname)
                except (KeyError, TypeError):
                    pass
            if all_locs:
                result.location_raw = " | ".join(all_locs)

    result.status = "success"
    return result


def _parse_address(result: CompanyResult, addr_text: str, branch_name: str = ""):
    """Parse address string like '3702 Hughes Ave, #6, LOS ANGELES, 90034, CA, USA'"""
    parts = [p.strip() for p in addr_text.split(",")]

    # Try to find zip, state, country from the end
    # Typical format: STREET, CITY, ZIP, STATE, COUNTRY
    if len(parts) >= 3:
        # Check last part for country
        last = parts[-1].strip()
        if last in ("USA", "US", "UK", "GB", "Canada", "Australia") or len(last) <= 3:
            if last in ("US", "USA"):
                result.country = "USA"
            elif last in ("GB", "UK"):
                result.country = "UK"
            else:
                result.country = last

        # Find state (2-letter code)
        for i, p in enumerate(parts):
            p = p.strip()
            if re.match(r'^[A-Z]{2}$', p) and p not in ("US", "UK", "GB"):
                result.state = p
                break

        # Find zip code
        for p in parts:
            z = re.search(r'\b(\d{5})(?:-\d{4})?\b', p.strip())
            if z:
                result.zip_code = z.group(1)
                break

        # City: from branch name or from address parts
        if branch_name:
            csz = re.match(r'([^,]+)', branch_name)
            if csz:
                result.city = csz.group(1).strip()
        if not result.city:
            # City is usually the part before zip/state
            for p in parts[1:]:
                p = p.strip()
                if (re.match(r'^[A-Z][A-Za-z\s]+$', p) and
                    len(p) > 2 and p not in ("USA", "US", "UK", "GB")):
                    result.city = p.title() if p.isupper() else p
                    break


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════════════════════════════════

class Agent:
    def __init__(self, agent_id: int, throttle: AdaptiveThrottle,
                 cookies: Dict[str, str], html_cache: HtmlCache):
        self.id = agent_id
        self.throttle = throttle
        self.cookies = cookies
        self.cache = html_cache
        self.request_count = 0
        self.ua = random.choice(USER_AGENTS)

    def _headers(self) -> dict:
        return {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://pro.imdb.com/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> Tuple[Optional[str], int]:
        last_status = 0
        for attempt in range(MAX_RETRIES + 1):
            await self.throttle.wait(self.id)
            try:
                async with session.get(url, headers=self._headers(),
                                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_TOTAL,
                                                                      sock_read=REQUEST_SOCK_READ),
                                        allow_redirects=True) as resp:
                    self.request_count += 1
                    if self.request_count % SESSION_ROTATE_EVERY == 0:
                        self.ua = random.choice(USER_AGENTS)

                    last_status = resp.status
                    if resp.status == 200:
                        if "signin" in str(resp.url).lower():
                            return None, 401
                        html = await resp.text()
                        await self.throttle.report_success()
                        return html, 200
                    elif resp.status == 429:
                        await self.throttle.report_rate_limit()
                        wait = min(MAX_BACKOFF, BACKOFF_BASE + random.uniform(5, 15))
                        log.warning(f"A{self.id}: 429 — wait {wait:.0f}s")
                        await asyncio.sleep(wait)
                    elif resp.status in (403, 404):
                        return None, resp.status
                    elif resp.status >= 500:
                        await self.throttle.report_server_error()
                        await asyncio.sleep(5)
                    else:
                        return None, resp.status
            except asyncio.TimeoutError:
                await asyncio.sleep(3)
            except aiohttp.ClientError:
                await asyncio.sleep(5)
            except Exception as e:
                log.error(f"A{self.id}: {type(e).__name__}: {e}")
                await asyncio.sleep(3)
        return None, last_status

    async def scrape_url(self, session: aiohttp.ClientSession, url: str) -> CompanyResult:
        # Check cache
        cached = self.cache.get(url)
        if cached:
            result = parse_company_page(cached, url)
            result.agent_id = self.id
            return result

        html, status = await self.fetch(session, url)
        if html and status == 200:
            self.cache.put(url, html)
            result = parse_company_page(html, url)
            result.agent_id = self.id
            result.http_status = status
            return result

        msgs = {401: "Session expired", 403: "Forbidden", 404: "Not found",
                429: "Rate limited", 0: "All retries failed"}
        return CompanyResult(url=url, agent_id=self.id,
                             status="not_found" if status == 404 else "error",
                             error_msg=msgs.get(status, f"HTTP {status}"),
                             http_status=status, scraped_at=datetime.now().isoformat())


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    def __init__(self, urls: List[str], cookies: Dict[str, str], resume: bool = False):
        self.all_urls = urls
        self.cookies = cookies
        self.throttle = AdaptiveThrottle()
        self.cache = HtmlCache()
        self.checkpoint = Checkpoint()
        self.results: List[CompanyResult] = []
        self.lock = asyncio.Lock()
        self.auth_dead = asyncio.Event()
        self.total_target = len(urls)
        self.counter = 0
        self.ok_count = 0
        self.err_count = 0
        self.start_time = time.time()

        # Always hydrate previously-completed results from the checkpoint so the
        # final CSV/JSON reflect ALL work, even on a plain re-run (URLs already in
        # the checkpoint are skipped by get_remaining regardless of --resume).
        valid_fields = set(CompanyResult.__dataclass_fields__)
        for url, data in self.checkpoint.completed.items():
            if not isinstance(data, dict):
                continue
            try:
                self.results.append(CompanyResult(
                    **{k: v for k, v in data.items() if k in valid_fields}))
            except (TypeError, ValueError):
                pass

    async def process_url(self, agent: Agent, session: aiohttp.ClientSession, url: str):
        if self.auth_dead.is_set():
            return
        result = await agent.scrape_url(session, url)
        if result.http_status == 401 or result.status == "auth_expired":
            self.auth_dead.set()
            log.error("🚨 AUTH EXPIRED — re-export cookies, then: python imdbpro_scraper.py --resume")
        await self.checkpoint.mark_done(url, asdict(result))
        async with self.lock:
            self.results.append(result)
            self.counter += 1
            if result.status == "success":
                self.ok_count += 1
            else:
                self.err_count += 1
            n = self.counter
            elapsed = time.time() - self.start_time
            rate = n / elapsed * 60 if elapsed > 0 else 0
            remaining = self.total_target - n
            eta_min = remaining / rate if rate > 0 else 0
            self._append_csv(result)
        icon = "✓" if result.status == "success" else "✗"
        name = (result.company_name or "?")[:30]
        log.info(f"[{n}/{self.total_target}] A{agent.id}: {icon} {name} │ {result.website or '-'} │ ✓{self.ok_count} ✗{self.err_count} │ {rate:.0f}/min │ ETA {eta_min:.0f}m")

    def _append_csv(self, result: CompanyResult):
        """Append a single result to CSV. Creates header on first write."""
        try:
            write_header = not os.path.exists(OUTPUT_CSV) or os.path.getsize(OUTPUT_CSV) == 0
            with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                if write_header:
                    w.writeheader()
                row = asdict(result)
                w.writerow({k: row.get(k, "") for k in CSV_FIELDS})
        except OSError as e:
            log.warning(f"⚠️  CSV append failed: {e}")

    async def agent_worker(self, agent_id: int, url_queue: asyncio.Queue):
        """Each agent creates and rotates its own session to avoid tarpit."""
        agent = Agent(agent_id, self.throttle, self.cookies, self.cache)
        urls_this_session = 0
        session = None
        timeouts_in_a_row = 0

        async def _new_session():
            nonlocal session
            if session and not session.closed:
                await session.close()
                await asyncio.sleep(1)  # let TCP connections fully close
            connector = aiohttp.TCPConnector(limit=3, ttl_dns_cache=300, force_close=True)
            jar = aiohttp.CookieJar(unsafe=True)
            session = aiohttp.ClientSession(connector=connector, cookie_jar=jar,
                                             cookies=self.cookies)
            log.info(f"A{agent_id}: 🔄 Fresh session")
            return session

        try:
            session = await _new_session()

            while not self.auth_dead.is_set():
                try:
                    url = url_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Rotate session every N URLs OR after consecutive timeouts
                if urls_this_session >= SESSION_REFRESH_EVERY or timeouts_in_a_row >= 2:
                    session = await _new_session()
                    urls_this_session = 0
                    timeouts_in_a_row = 0
                    await asyncio.sleep(random.uniform(2, 5))  # brief pause after rotate

                try:
                    await asyncio.wait_for(
                        self.process_url(agent, session, url),
                        timeout=OUTER_TIMEOUT  # watchdog — see config note
                    )
                    urls_this_session += 1
                    timeouts_in_a_row = 0
                except asyncio.TimeoutError:
                    timeouts_in_a_row += 1
                    log.warning(f"A{agent_id}: ⏰ Watchdog timeout #{timeouts_in_a_row} on {url}")
                    # Only record an error if process_url didn't already finish this
                    # URL (it may have completed just as the watchdog fired).
                    if not self.checkpoint.is_done(url):
                        result = CompanyResult(url=url, agent_id=agent_id, status="error",
                                               error_msg="Watchdog timeout",
                                               scraped_at=datetime.now().isoformat())
                        await self.checkpoint.mark_done(url, asdict(result))
                        async with self.lock:
                            self.results.append(result)
                except Exception as e:
                    log.error(f"A{agent_id}: 💥 {url[:60]} — {type(e).__name__}: {e}")
                    if not self.checkpoint.is_done(url):
                        result = CompanyResult(url=url, agent_id=agent_id, status="error",
                                               error_msg=f"Worker error: {e}",
                                               scraped_at=datetime.now().isoformat())
                        try:
                            await self.checkpoint.mark_done(url, asdict(result))
                            async with self.lock:
                                self.results.append(result)
                        except Exception:
                            pass
                finally:
                    url_queue.task_done()
        finally:
            if session and not session.closed:
                await session.close()
        log.info(f"A{agent_id}: done ({agent.request_count} reqs)")

    async def run(self):
        remaining = self.checkpoint.get_remaining(self.all_urls)
        total = len(self.all_urls)
        done_already = total - len(remaining)

        print(f"\n{'═' * 65}")
        print(f"  IMDbPro Scraper v2 SPEED MODE — {total:,} total URLs")
        print(f"  Done: {done_already:,} │ Remaining: {len(remaining):,}")
        print(f"  Agents: {NUM_AGENTS} │ Delay: {INITIAL_DELAY}s/agent │ Cache: {self.cache.size:,}")
        # ETA: each agent does ~7 URLs per session cycle (~28s fetch + ~4s rotate = ~32s per 7)
        # 6 agents = ~42 URLs per 32s = ~78/min
        eta_min = len(remaining) / 78
        print(f"  ETA: ~{eta_min:.0f} minutes ({eta_min/60:.1f} hours)")
        print(f"{'═' * 65}\n")

        if not remaining:
            log.info("All URLs already processed. Use --reparse to re-run parser on cached HTML.")
            return

        random.shuffle(remaining)
        self.total_target = len(remaining)
        self.start_time = time.time()

        log.info(f"🚀 Launching {NUM_AGENTS} agents for {len(remaining)} URLs")
        log.info(f"   Each agent: own session, rotated every {SESSION_REFRESH_EVERY} URLs")

        q = asyncio.Queue()
        for u in remaining:
            q.put_nowait(u)

        tasks = []
        for i in range(min(NUM_AGENTS, len(remaining))):
            tasks.append(asyncio.create_task(self.agent_worker(i+1, q)))
            await asyncio.sleep(random.uniform(1, 3))  # stagger agent starts

        try:
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.warning("⏹  Interrupted — cancelling workers and saving progress…")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            await self.checkpoint.save()
            done = len(self.checkpoint.completed)
            ok = sum(1 for r in self.checkpoint.completed.values()
                     if isinstance(r, dict) and r.get("status") == "success")
            err = done - ok
            log.info(f"🏁 DONE — {done:,}/{total:,} │ ✓{ok:,} ✗{err:,} │ {self.throttle.stats}")
            self._write_final_csv()

    def _rows(self) -> List[dict]:
        """Durable, de-duplicated result rows. The checkpoint (keyed by URL) is the
        source of truth — it has every processed URL exactly once. Fall back to the
        in-memory results only if the checkpoint is somehow empty."""
        if self.checkpoint.completed:
            return [r for r in self.checkpoint.completed.values() if isinstance(r, dict)]
        return [asdict(r) for r in self.results]

    def _write_final_csv(self):
        """Write all results to a final clean CSV (deduped, from the checkpoint)."""
        rows = self._rows()
        if not rows:
            return
        out = OUTPUT_CSV.replace(".csv", "_final.csv")
        try:
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                w.writeheader()
                for row in rows:
                    w.writerow({k: row.get(k, "") for k in CSV_FIELDS})
        except OSError as e:
            log.error(f"⚠️  Final CSV write failed: {e}")
            return
        ok = sum(1 for r in rows if r.get("status") == "success")
        err = len(rows) - ok
        print(f"\n{'═' * 65}")
        print(f"  ✅ DONE — {len(rows):,} total │ ✓{ok:,} │ ✗{err:,}")
        print(f"  CSV:  {OUTPUT_CSV} (live) + {out} (final)")
        print(f"  Cache: {self.cache.size:,} HTML files saved")
        print(f"{'═' * 65}\n")

    def export(self):
        rows = self._rows()
        if not rows:
            return

        try:
            with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                w.writeheader()
                for row in rows:
                    w.writerow({k: row.get(k, "") for k in CSV_FIELDS})

            with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2)
        except OSError as e:
            log.error(f"⚠️  Export failed: {e}")
            return

        ok = sum(1 for r in rows if r.get("status") == "success")
        err = len(rows) - ok
        print(f"\n{'═' * 65}")
        print(f"  ✅ DONE — {len(rows):,} total │ ✓{ok:,} │ ✗{err:,}")
        print(f"  CSV:  {OUTPUT_CSV}")
        print(f"  JSON: {OUTPUT_JSON}")
        print(f"  Cache: {self.cache.size:,} HTML files saved")
        print(f"{'═' * 65}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# REPARSE (no network — just re-run parser on cached HTML)
# ═══════════════════════════════════════════════════════════════════════════════

def reparse_from_cache(urls: List[str]):
    cache = HtmlCache()
    results = []
    for url in urls:
        html = cache.get(url)
        if html:
            results.append(parse_company_page(html, url))

    out = OUTPUT_CSV.replace(".csv", "_reparsed.csv")
    fields = ["company_name", "category", "companymeter", "website", "phone", "email",
              "address", "city", "state", "zip_code", "country", "location_raw",
              "staff_count", "client_count", "url", "status"]

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            row = asdict(r)
            w.writerow({k: row.get(k, "") for k in fields})

    ok = sum(1 for r in results if r.status == "success")
    log.info(f"✅ Re-parsed {len(results)} pages (✓{ok}) → {out}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global NUM_AGENTS, INITIAL_DELAY

    import argparse
    p = argparse.ArgumentParser(description="IMDbPro Company Scraper v2")
    p.add_argument("--input", "-i", default="urls.csv")
    p.add_argument("--resume", "-r", action="store_true")
    p.add_argument("--reparse", action="store_true")
    p.add_argument("--cookies", "-c", default=COOKIE_FILE)
    p.add_argument("--agents", type=int, default=NUM_AGENTS)
    p.add_argument("--delay", type=float, default=INITIAL_DELAY)
    args = p.parse_args()

    NUM_AGENTS = args.agents
    INITIAL_DELAY = args.delay

    if not os.path.exists(args.input):
        log.error(f"Input not found: {args.input}")
        sys.exit(1)

    urls = load_urls(args.input)

    if args.reparse:
        reparse_from_cache(urls)
        return

    if not os.path.exists(args.cookies):
        log.error(f"Cookies not found: {args.cookies}")
        sys.exit(1)

    cookies = parse_netscape_cookies(args.cookies)
    orch = Orchestrator(urls, cookies, resume=args.resume)
    try:
        asyncio.run(orch.run())
    except KeyboardInterrupt:
        log.warning("⏹  Stopped by user. Progress saved — resume with: "
                    "python imdbpro_scraper.py --resume")
    finally:
        orch.export()


if __name__ == "__main__":
    main()

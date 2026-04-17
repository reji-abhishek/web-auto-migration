import os
from dotenv import load_dotenv
from urllib.parse import urlparse

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
import re
import requests
from bs4 import BeautifulSoup

# ─── Available Departments (fetched dynamically) ─────────────

def _fetch_departments():
    """Scrape department codes and names from the source website."""
    url = os.getenv("SOURCE_DOMAIN") or "https://cce.edu.in"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  ⚠ Could not fetch departments from {url}: {e}")
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    depts = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/department/([^/]+)/", a["href"])
        if m:
            code = m.group(1)
            name = a.get_text(strip=True)
            # Clean up whitespace from multi-line link text
            name = re.sub(r"\s+", " ", name).strip()
            # Prefer names that start with "Department" or are longer
            if code not in depts or (
                "Department" in name and "Department" not in depts[code]
            ) or (
                "Department" in name and len(name) > len(depts[code])
            ):
                if name:
                    depts[code] = name
    return depts


# Fetched once at import time (cached for the session)
DEPARTMENTS = _fetch_departments()

# ─── CMS Admin Portal ────────────────────────────────────────
def _env(name):
    value = os.getenv(name)
    return value.strip() if isinstance(value, str) else None


def _derive_cms_base(cms_base, login_url, create_page):
    if cms_base:
        return cms_base.rstrip("/")

    if login_url and "/login" in login_url:
        return login_url.rsplit("/login", 1)[0].rstrip("/")

    if create_page and "/departments/create" in create_page:
        return create_page.rsplit("/departments/create", 1)[0].rstrip("/")

    if login_url:
        p = urlparse(login_url)
        if p.scheme and p.netloc:
            # Fallback if login path is non-standard: keep origin + parent path
            parent = p.path.rsplit("/", 1)[0]
            return f"{p.scheme}://{p.netloc}{parent}".rstrip("/")

    return None


CMS_BASE = _derive_cms_base(
    _env("CMS_BASE"),
    _env("LOGIN_URL"),
    _env("CREATE_PAGE"),
)
LOGIN_URL = _env("LOGIN_URL") or (f"{CMS_BASE}/login" if CMS_BASE else "")

# Per-type CMS URLs
CMS_URLS = {
    "department": {
        "create": f"{CMS_BASE}/departments/create" if CMS_BASE else "",
        "list":   f"{CMS_BASE}/departments" if CMS_BASE else "",
    },
    "faculty": {
        "create": f"{CMS_BASE}/faculties/create" if CMS_BASE else "",
        "list":   f"{CMS_BASE}/faculties" if CMS_BASE else "",
    },
    "magazine": {
        "create": f"{CMS_BASE}/magazines/create" if CMS_BASE else "",
        "list":   f"{CMS_BASE}/magazines" if CMS_BASE else "",
    },
}

# Legacy alias used by department pipeline
CREATE_PAGE = CMS_URLS["department"]["create"]

# ─── Source Website ───────────────────────────────────────────
SOURCE_DOMAIN = os.getenv("SOURCE_DOMAIN") or "https://cce.edu.in"
SOURCE_URL    = ""  # Set at runtime via set_department()

def set_department(dept_code):
    """Set the active department. Call before importing scrapers."""
    global SOURCE_URL
    SOURCE_URL = f"{SOURCE_DOMAIN}/department/{dept_code}"

# ─── Credentials ─────────────────────────────────────────────
EMAIL    = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

# ─── Google Gemini API ────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDtb9glgvwEt62vne0Z8eJWOb90hFWLnu4")
AI_MODEL       = "gemini-2.0-flash"

# ─── Paths ────────────────────────────────────────────────────
BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR        = os.path.join(BASE_DIR, "assets")
FORM_SCHEMA_FILE  = os.path.join(BASE_DIR, "form_schema.json")
SCRAPE_PLAN_FILE  = os.path.join(BASE_DIR, "scrape_plan.json")
SCRAPED_DATA_FILE = os.path.join(BASE_DIR, "scraped_data.json")

# Per-type data files
FACULTY_DATA_FILE  = os.path.join(BASE_DIR, "faculty_data.json")
MAGAZINE_DATA_FILE = os.path.join(BASE_DIR, "magazine_data.json")

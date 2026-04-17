"""
Department Co-Curricular Events Migration

Pipeline:
1. Choose department (code input or interactive prompt).
2. Login to CMS.
3. Open that department's edit page.
4. Click "Department Cocurricular Events".
5. Scrape source events from SOURCE_URL/events.
6. Create and fill all missing event entries in CMS.
"""

import os
import re
import time
import json
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import config
from browser import get_driver, login
from cms_inspector import inspect_form
from config import ASSETS_DIR, CMS_URLS
from compress import compress_image
from form_filler import (
    _fill_text,
    _fill_textarea,
    _fill_select,
    _fill_rich_text,
    _fill_check_radio,
)

HEADERS = {"User-Agent": "Mozilla/5.0"}
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), "cocurricular_progress.json")
AUTO_FAIL_LOG_FILE = os.path.join(os.path.dirname(__file__), "cocurricular_auto_failures.json")
INTERRUPT_FILE = os.path.join(os.path.dirname(__file__), "cocurricular_stop.txt")
SKIP_LOG_FILE = os.path.join(os.path.dirname(__file__), "cocurricular_skipped_events.txt")


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _title_key(s):
    """Stable matching key for title comparisons across source/CMS."""
    s = _norm(s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_checkpoint_keys(dept_code):
    if not os.path.isfile(CHECKPOINT_FILE):
        return set()
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get((dept_code or "").upper(), [])
        return {_title_key(x) for x in items if _title_key(x)}
    except Exception:
        return set()


def _save_checkpoint_key(dept_code, title):
    dept = (dept_code or "").upper()
    if not dept:
        return
    key = _title_key(title)
    if not key:
        return
    try:
        if os.path.isfile(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
    except Exception:
        data = {}

    current = set(data.get(dept, []))
    current.add(key)
    data[dept] = sorted(current)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _append_auto_failure(dept_code, event, reason):
    try:
        if os.path.isfile(AUTO_FAIL_LOG_FILE):
            with open(AUTO_FAIL_LOG_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, list):
                payload = []
        else:
            payload = []
    except Exception:
        payload = []

    payload.append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "department": (dept_code or "").upper(),
            "title": (event or {}).get("title", ""),
            "date": (event or {}).get("date", ""),
            "category": (event or {}).get("category_label", "") or _humanize_category((event or {}).get("category_type", "")),
            "reason": reason,
        }
    )

    with open(AUTO_FAIL_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _append_skip_log(dept_code, event, reason="user_skip"):
    title = (event or {}).get("title", "")
    date = (event or {}).get("date", "")
    category = (event or {}).get("category_label", "") or _humanize_category((event or {}).get("category_type", ""))
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"{ts} | {(dept_code or '').upper()} | {title} | {date} | {category} | {reason}\n"
    with open(SKIP_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def _slugify(text):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug[:120]


def _humanize_category(category_type):
    mapping = {
        "workshops_seminars": "Workshops / Seminars",
        "addons": "Value Added Courses",
        "iv": "Industrial Visit",
        "competitions": "Competitions",
        "all": "All",
    }
    key = (category_type or "").strip().lower()
    if key in mapping:
        return mapping[key]
    return re.sub(r"[_\-]+", " ", key).title()


def _to_cms_date(raw_date):
    """Convert source dates like 'Oct. 16, 2022' -> '10/16/2022'."""
    s = re.sub(r"\s+", " ", (raw_date or "")).strip()
    if not s:
        return ""

    # Normalize common month variants from source site.
    s = s.replace("Sept.", "Sep.").replace("Sept ", "Sep ")

    candidates = [
        "%m/%d/%Y",   # 10/16/2022
        "%Y-%m-%d",   # 2022-10-16
        "%b. %d, %Y",  # Oct. 16, 2022
        "%b %d, %Y",   # Oct 16, 2022
        "%B %d, %Y",   # October 16, 2022
        "%d %b %Y",    # 16 Oct 2022
        "%d %B %Y",    # 16 October 2022
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(s, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue

    # Regex fallback for odd spacing/punctuation.
    m = re.search(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})", s)
    if not m:
        return s
    mon, day, year = m.groups()
    mon_key = mon.strip().lower()[:3]
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    month = month_map.get(mon_key)
    if not month:
        return s
    try:
        return datetime(int(year), int(month), int(day)).strftime("%m/%d/%Y")
    except ValueError:
        return s


def _batch_label_from_event_date(raw_date):
    """Map event year YYYY -> batch label YYYY-YYYY+1."""
    cms_date = _to_cms_date(raw_date)
    year = None
    try:
        year = datetime.strptime(cms_date, "%m/%d/%Y").year
    except Exception:
        m = re.search(r"\b(20\d{2})\b", raw_date or "")
        if m:
            year = int(m.group(1))
    if not year:
        return ""
    return f"{year}-{year + 1}"


def _pick_department():
    from config import DEPARTMENTS

    if not DEPARTMENTS:
        code = input("\nEnter department code (e.g. CSE, EEE): ").strip().upper()
        return code

    codes = list(DEPARTMENTS.keys())
    print("\n" + "=" * 60)
    print("  SELECT DEPARTMENT")
    print("=" * 60)
    for i, code in enumerate(codes, 1):
        print(f"  {i}. {code} — {DEPARTMENTS[code]}")

    while True:
        choice = input("\nEnter number or code: ").strip().upper()
        if choice in DEPARTMENTS:
            return choice
        try:
            idx = int(choice)
            if 1 <= idx <= len(codes):
                return codes[idx - 1]
        except ValueError:
            pass
        print("✘ Invalid choice. Try again.")


def _ask_sort_order():
    while True:
        raw = input("\nEnter department sort order (n): ").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        print("✘ Invalid sort order. Enter a positive integer.")


def _ask_event_start_sort_order(default_value=1):
    while True:
        raw = input(
            f"\nEnter starting event sort_order (default {default_value}): "
        ).strip()
        if not raw:
            return int(default_value)
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        print("✘ Invalid start sort order. Enter a positive integer.")


def _extract_first_int(text):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _row_numeric_candidates(row):
    nums = set()
    cells = row.find_elements(By.TAG_NAME, "td")
    for cell in cells:
        for raw in (
            cell.text,
            cell.get_attribute("data-order"),
            cell.get_attribute("data-sort"),
        ):
            n = _extract_first_int(raw or "")
            if n is not None:
                nums.add(n)
        for inp in cell.find_elements(By.CSS_SELECTOR, "input, span"):
            n = _extract_first_int((inp.get_attribute("value") or inp.text or ""))
            if n is not None:
                nums.add(n)
    return nums


def _find_sort_order_col_index(driver):
    headers = driver.find_elements(By.CSS_SELECTOR, "table thead th")
    for i, th in enumerate(headers):
        h = _norm(th.text)
        if "sort order" in h:
            return i
    return None


def _find_title_col_index(driver):
    headers = driver.find_elements(By.CSS_SELECTOR, "table thead th")
    for i, th in enumerate(headers):
        h = _norm(th.text)
        if any(k in h for k in ("title", "event title", "name", "event")):
            return i
    return None


def _click_edit_from_row(driver, row):
    links = row.find_elements(
        By.XPATH,
        ".//a[contains(@href,'/departments/') and contains(@href,'/edit')]",
    )
    if not links:
        links = row.find_elements(
            By.XPATH,
            ".//a[contains(translate(normalize-space(.), 'EDIT', 'edit'), 'edit')]",
        )
    if not links:
        icon_links = row.find_elements(
            By.CSS_SELECTOR,
            "a i.fa-edit, a i.fa-pencil, a i.mdi-pencil, button i.fa-edit, button i.fa-pencil",
        )
        for icon in icon_links:
            try:
                links.append(icon.find_element(By.XPATH, "./ancestor::a[1]"))
            except Exception:
                try:
                    links.append(icon.find_element(By.XPATH, "./ancestor::button[1]"))
                except Exception:
                    pass

    for link in links:
        href = link.get_attribute("href")
        if href:
            return href
        try:
            driver.execute_script("arguments[0].click();", link)
            time.sleep(1)
            return driver.current_url
        except Exception:
            continue
    return None


def _go_next_page(driver, prev_first_row=None):
    wait = WebDriverWait(driver, 10)
    candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "a[rel='next'], .pagination a[aria-label*='Next'], .pagination a[aria-label*='next'], .pagination .next a, .dataTables_paginate a.next",
    )
    for nxt in candidates:
        try:
            parent_cls = (nxt.find_element(By.XPATH, "..").get_attribute("class") or "").lower()
        except Exception:
            parent_cls = ""
        if "disabled" in parent_cls:
            continue
        if _norm(nxt.get_attribute("aria-disabled")) == "true":
            continue

        txt = _norm(nxt.text)
        aria = _norm(nxt.get_attribute("aria-label"))
        href = nxt.get_attribute("href") or ""
        if not (("next" in txt) or ("next" in aria) or ("page=" in href) or ("»" in txt) or (txt == ">")):
            continue
        try:
            driver.execute_script("arguments[0].click();", nxt)
            if prev_first_row is not None:
                wait.until(EC.staleness_of(prev_first_row))
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
            time.sleep(1)
            return True
        except Exception:
            continue
    return False


def _find_department_edit_url(driver, dept_sort_order):
    wait = WebDriverWait(driver, 15)
    driver.get(CMS_URLS["department"]["list"])
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)

    target = int(dept_sort_order)
    sort_idx = _find_sort_order_col_index(driver)
    if sort_idx is None:
        print("  ⚠ Could not detect 'Sort Order' column header. Falling back to row scan.")

    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        first_row = rows[0] if rows else None
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            row_sort = None

            if sort_idx is not None and sort_idx < len(cells):
                cell = cells[sort_idx]
                row_sort = _extract_first_int(cell.text)
                if row_sort is None:
                    # sometimes sort order is in hidden inputs
                    for inp in cell.find_elements(By.CSS_SELECTOR, "input, span"):
                        row_sort = _extract_first_int(inp.get_attribute("value") or inp.text or "")
                        if row_sort is not None:
                            break
            else:
                # fallback: scan all cells and pick the first numeric token
                for cell in cells:
                    row_sort = _extract_first_int(cell.text)
                    if row_sort is not None:
                        break

            if row_sort != target:
                continue

            href = _click_edit_from_row(driver, row)
            if href:
                return href

        if not _go_next_page(driver, prev_first_row=first_row):
            break

    # Fallback 1: scan every row by all numeric clues in the row
    driver.get(CMS_URLS["department"]["list"])
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)
    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        first_row = rows[0] if rows else None
        for row in rows:
            candidates = _row_numeric_candidates(row)
            if target in candidates:
                href = _click_edit_from_row(driver, row)
                if href:
                    return href
        if not _go_next_page(driver, prev_first_row=first_row):
            break

    # Fallback 2: use table search box if present
    driver.get(CMS_URLS["department"]["list"])
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(1)
    search_inputs = driver.find_elements(
        By.CSS_SELECTOR,
        "input[type='search'], input[placeholder*='Search'], input[name*='search']",
    )
    if search_inputs:
        try:
            s = search_inputs[0]
            s.clear()
            s.send_keys(str(target))
            time.sleep(1.5)
            rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            for row in rows:
                href = _click_edit_from_row(driver, row)
                if href:
                    return href
        except Exception:
            pass

    return None


def _click_cocurricular_events(driver):
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(1)

    candidates = driver.find_elements(By.XPATH, "//a|//button")
    scored = []
    for el in candidates:
        text = _norm(el.text)
        if not text:
            continue
        score = 0
        if "co" in text and "curricular" in text:
            score += 2
        if "event" in text:
            score += 2
        if "department" in text:
            score += 1
        if score:
            scored.append((score, el))

    scored.sort(key=lambda x: x[0], reverse=True)
    for _, el in scored:
        try:
            href = el.get_attribute("href")
            if href:
                driver.get(href)
            else:
                driver.execute_script("arguments[0].click();", el)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(2)
            return driver.current_url
        except Exception:
            continue

    return None


def _parse_event_cards(soup, category_type=None, category_label=None):
    cards = []
    seen = set()

    content = soup.find("section", id="content") or soup
    for h3 in content.find_all("h3"):
        title = re.sub(r"\s+", " ", h3.get_text(" ", strip=True))
        if not title:
            continue
        low = title.lower()
        if low in {"co curricular events", "departments"}:
            continue

        wrapper = h3.find_parent("div", class_=lambda c: c and "group" in c)
        if not wrapper:
            wrapper = h3.find_parent("div")
        if not wrapper:
            continue

        title_key = _norm(title)
        if title_key in seen:
            continue
        seen.add(title_key)

        date_el = wrapper.select_one("p.text-xs")
        date_txt = re.sub(r"\s+", " ", date_el.get_text(" ", strip=True)) if date_el else ""

        desc_txt = ""
        for sib in h3.find_next_siblings("p"):
            txt = re.sub(r"\s+", " ", sib.get_text(" ", strip=True))
            if txt and txt != date_txt:
                desc_txt = txt
                break

        img_el = wrapper.find("img", src=True)
        img_url = urljoin(config.SOURCE_URL, img_el["src"]) if img_el else ""

        cards.append(
            {
                "title": title,
                "date": date_txt,
                "description": desc_txt,
                "image_url": img_url,
                "category_type": (category_type or "").strip(),
                "category_label": (category_label or _humanize_category(category_type)),
            }
        )

    return cards


def _discover_event_category_urls(soup, events_base_url):
    discovered = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(events_base_url, href)
        if "/events" not in full or "type=" not in full:
            continue
        q = parse_qs(urlparse(full).query)
        typ = (q.get("type", [""])[0] or "").strip()
        if not typ or typ.upper() == "ALL":
            continue
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        discovered[typ.lower()] = {
            "url": full,
            "type": typ.lower(),
            "label": label or _humanize_category(typ),
        }
    return list(discovered.values())


def _scrape_source_events():
    base_url = f"{config.SOURCE_URL.rstrip('/')}/events"
    r = requests.get(base_url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    category_urls = _discover_event_category_urls(soup, base_url)
    events = []
    seen = set()

    if not category_urls:
        events = _parse_event_cards(soup, category_type="all", category_label="All")
    else:
        for cat in category_urls:
            try:
                rc = requests.get(cat["url"], headers=HEADERS, timeout=25)
                rc.raise_for_status()
                csoup = BeautifulSoup(rc.text, "html.parser")
                cards = _parse_event_cards(
                    csoup,
                    category_type=cat["type"],
                    category_label=cat["label"],
                )
                for card in cards:
                    key = (_norm(card.get("title")), _norm(card.get("date")))
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(card)
            except Exception:
                continue

    cat_count = len({e.get("category_type") for e in events if e.get("category_type")})
    print(f"✔ Scraped {len(events)} co-curricular events from source ({cat_count} categories)")
    return events


def _download_images(events):
    out_dir = os.path.join(ASSETS_DIR, "cocurricular_events")
    os.makedirs(out_dir, exist_ok=True)

    for i, event in enumerate(events, 1):
        url = event.get("image_url")
        if not url:
            event["image_path"] = ""
            continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=40)
            resp.raise_for_status()
            ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
            ext = ext if len(ext) <= 8 else ".jpg"
            fname = f"{i:03d}_{_slugify(event['title'])[:50]}{ext}"
            path = os.path.join(out_dir, fname)
            with open(path, "wb") as f:
                f.write(resp.content)
            # Keep image <= 250KB before CMS upload for safer headroom.
            path = compress_image(path, target_kb=250)
            # Second pass safety if still above CMS validator limit.
            if os.path.isfile(path) and os.path.getsize(path) > 250000:
                path = compress_image(path, target_kb=220)
            event["image_path"] = path
        except Exception:
            event["image_path"] = ""

    return events


def _extract_title_from_row(row):
    # Prefer non-action links (title is commonly rendered as an <a>).
    links = row.find_elements(By.XPATH, ".//a[normalize-space()]")
    for a in links:
        txt = re.sub(r"\s+", " ", (a.text or "")).strip()
        low = _norm(txt)
        if not txt:
            continue
        if low in ("edit", "delete", "view"):
            continue
        if len(low) > 6 and re.search(r"[a-zA-Z]", txt):
            return low

    # Fallback: choose best candidate from cells.
    cells = row.find_elements(By.TAG_NAME, "td")
    candidates = []
    for cell in cells:
        txt = re.sub(r"\s+", " ", (cell.text or "")).strip()
        low = _norm(txt)
        if not low:
            continue
        if low.isdigit():
            continue
        if low in ("enabled", "disabled", "active", "inactive"):
            continue
        if len(low) <= 6:
            continue
        if not re.search(r"[a-zA-Z]", txt):
            continue
        candidates.append(low)

    if not candidates:
        return ""
    # Usually title is the longest meaningful text in the row.
    return max(candidates, key=len)


def _read_existing_titles_from_list(driver, events_list_url):
    title_keys = set()
    wait = WebDriverWait(driver, 15)
    driver.get(events_list_url)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(1)
    title_col_idx = _find_title_col_index(driver)

    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        first_row = rows[0] if rows else None

        for row in rows:
            title = ""
            if title_col_idx is not None:
                cells = row.find_elements(By.TAG_NAME, "td")
                if title_col_idx < len(cells):
                    title = re.sub(r"\s+", " ", (cells[title_col_idx].text or "")).strip()
            if not title:
                title = _extract_title_from_row(row)
            if title:
                key = _title_key(title)
                if key:
                    title_keys.add(key)

        if not _go_next_page(driver, prev_first_row=first_row):
            break

    return title_keys


def _open_create_form(driver):
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    btns = driver.find_elements(By.XPATH, "//a|//button")

    for b in btns:
        txt = _norm(b.text)
        href = (b.get_attribute("href") or "").lower()
        if ("create" in txt or "add" in txt or "new" in txt or "/create" in href):
            try:
                if href:
                    driver.get(b.get_attribute("href"))
                else:
                    driver.execute_script("arguments[0].click();", b)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                time.sleep(1)
                return True
            except Exception:
                continue
    return False


def _pick_select_label_or_value(field_name, event, label_text, field=None):
    n = _norm(field_name + " " + label_text)
    if "status" in n:
        return "1"
    if "department" in n:
        return ""
    if "batch" in n or "year" in n:
        batch_label = _batch_label_from_event_date(event.get("date", ""))
        if not batch_label:
            return ""
        options = (field or {}).get("options", [])
        if options:
            for opt in options:
                txt = _norm(opt.get("text", ""))
                val = _norm(opt.get("value", ""))
                if txt == _norm(batch_label) or val == _norm(batch_label):
                    return opt.get("value", "") or opt.get("text", "")
            # relaxed match
            for opt in options:
                txt = _norm(opt.get("text", ""))
                if batch_label in (opt.get("text", "") or "") or _norm(batch_label) in txt:
                    return opt.get("value", "") or opt.get("text", "")
        return batch_label
    if "type" in n or "category" in n:
        desired = [
            _norm(event.get("category_label", "")),
            _norm(_humanize_category(event.get("category_type", ""))),
            _norm(event.get("category_type", "")),
        ]
        desired = [d for d in desired if d]
        options = (field or {}).get("options", [])
        if options and desired:
            for opt in options:
                txt = _norm(opt.get("text", ""))
                val = _norm(opt.get("value", ""))
                if not txt and not val:
                    continue
                if any((d == txt) or (d == val) for d in desired):
                    return opt.get("value", "") or opt.get("text", "")
            # fuzzy keyword mapping
            aliases = {
                "workshops_seminars": ("workshop", "seminar"),
                "addons": ("value", "add on", "addon"),
                "iv": ("industrial", "visit"),
                "competitions": ("competition", "contest"),
            }
            cat_type = (event.get("category_type") or "").lower()
            keys = aliases.get(cat_type, ())
            if keys:
                for opt in options:
                    txt = _norm(opt.get("text", ""))
                    if txt and any(k in txt for k in keys):
                        return opt.get("value", "") or opt.get("text", "")
        # fallback: text match if options not available
        return event.get("category_label", "") or _humanize_category(event.get("category_type", ""))
    return ""


def _build_field_value(field, event, index):
    name = field.get("name", "")
    label = field.get("label", "")
    ftype = field.get("type", "text")
    key = _norm(f"{name} {label}")

    if ftype == "file":
        return event.get("image_path", "")
    if "slug" in key:
        return _slugify(event.get("title", ""))
    if "date" in key:
        return _to_cms_date(event.get("date", ""))
    if any(k in key for k in ("title", "name", "heading")):
        return event.get("title", "")
    if "description" in key or "content" in key or "details" in key:
        desc = event.get("description", "")
        if ftype == "rich_text_editor":
            return f"<p>{desc}</p>" if desc else ""
        return desc
    if "sort" in key or "order" in key:
        return str(index)
    if ftype == "select":
        return _pick_select_label_or_value(name, event, label, field=field)
    if ftype in ("checkbox", "radio"):
        return field.get("value", "")
    return ""


def _auto_crop_uploaded_image(driver):
    """Try to confirm crop modal automatically if CMS opens one."""
    deadline = time.time() + 8.0
    # Prefer explicit "No Crop" actions when available.
    no_crop_xpaths = [
        "//button[contains(translate(normalize-space(.),'NO CROP','no crop'),'no crop')]",
        "//a[contains(translate(normalize-space(.),'NO CROP','no crop'),'no crop')]",
        "//button[contains(translate(normalize-space(.),'WITHOUT CROP','without crop'),'without crop')]",
        "//button[contains(translate(normalize-space(.),'SKIP CROP','skip crop'),'skip crop')]",
        "//a[contains(translate(normalize-space(.),'SKIP CROP','skip crop'),'skip crop')]",
    ]
    while time.time() < deadline:
        for xp in no_crop_xpaths:
            try:
                for btn in driver.find_elements(By.XPATH, xp):
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1.0)
                        return True
            except Exception:
                continue

        # Then try common explicit crop/save buttons.
        crop_xpaths = [
            "//button[contains(translate(normalize-space(.),'CROP IMAGE','crop image'),'crop')]",
            "//button[contains(translate(normalize-space(.),'CROP','crop'),'crop')]",
            "//a[contains(translate(normalize-space(.),'CROP','crop'),'crop')]",
            "//button[contains(translate(normalize-space(.),'DONE','done'),'done')]",
            "//button[contains(translate(normalize-space(.),'SAVE','save'),'save')]",
            "//button[contains(translate(normalize-space(.),'APPLY','apply'),'apply')]",
            "//button[contains(translate(normalize-space(.),'OK','ok'),'ok')]",
        ]
        for xp in crop_xpaths:
            try:
                for btn in driver.find_elements(By.XPATH, xp):
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1.0)
                        return True
            except Exception:
                continue
        time.sleep(0.25)

    # Fallback: click the last visible button in crop/modal containers.
    try:
        clicked = driver.execute_script(
            """
            const kws = ['no crop','nocrop','without crop','skip crop','crop','save','apply','done','ok','confirm'];
            const roots = Array.from(document.querySelectorAll(
              '.modal.show,.modal,[role="dialog"],.swal2-popup,.cropper-container'
            ));
            const searchRoot = roots.length ? roots[roots.length - 1] : document;
            const candidates = Array.from(searchRoot.querySelectorAll('button,a,input[type="button"],input[type="submit"]'));
            const visible = candidates.filter(el => {
              const st = window.getComputedStyle(el);
              const r = el.getBoundingClientRect();
              return st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0 && r.height > 0;
            });
            for (let i = visible.length - 1; i >= 0; i--) {
              const el = visible[i];
              const text = (el.innerText || el.value || '').toLowerCase().trim();
              if (kws.some(k => text.includes(k))) {
                el.click();
                return true;
              }
            }
            return false;
            """
        )
        if clicked:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    return False


def _fill_file_autocrop(driver, name, filepath):
    if not filepath or not os.path.isfile(filepath):
        print(f"  ✘ file        {name}  →  file not found: {filepath}")
        return False
    abs_path = os.path.abspath(filepath)
    try:
        file_inputs = driver.find_elements(
            By.CSS_SELECTOR, f'input[type="file"][name="{name}"]'
        )
        if not file_inputs:
            file_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        if not file_inputs:
            print(f"  ✘ file        {name}  →  no <input type=file> found")
            return False

        el = file_inputs[0]
        driver.execute_script(
            """
            var el = arguments[0];
            el.style.display = 'block';
            el.style.visibility = 'visible';
            el.style.opacity = '1';
            el.style.height = 'auto';
            el.style.width = 'auto';
            el.style.position = 'relative';
            el.removeAttribute('hidden');
            """,
            el,
        )
        time.sleep(0.2)
        el.send_keys(abs_path)
        time.sleep(1.2)
        cropped = _auto_crop_uploaded_image(driver)
        if not cropped:
            print("    ⚠ Auto-crop not detected. Please crop manually if modal is open.")
        print(f"  ✔ file        {name}  →  {os.path.basename(abs_path)}")
        return True
    except Exception as e:
        print(f"  ✘ file        {name}  →  {e}")
        return False


def _fill_event_form(driver, schema, event, index):
    field_lookup = schema.get("fields", [])
    filled = 0
    failed = 0

    skip_prefixes = (
        "layout",
        "topbar-color",
        "sidebar-size",
        "sidebar-color",
        "layout-direction",
        "layout-mode",
        "layout-width",
        "layout-position",
    )

    rich_text_names = {
        f.get("name", "")
        for f in field_lookup
        if f.get("type") == "rich_text_editor" and f.get("name")
    }

    for field in field_lookup:
        name = field.get("name", "")
        if not name:
            continue
        if name.startswith(skip_prefixes):
            continue

        ftype = field.get("type", "text")
        if name in rich_text_names and ftype == "textarea":
            # Same logical field is represented by rich text editor + hidden textarea.
            continue

        value = _build_field_value(field, event, index)

        if value in (None, "") and ftype not in ("select",):
            continue

        if ftype == "file":
            ok = _fill_file_autocrop(driver, name, value)
        elif ftype == "rich_text_editor":
            ok = _fill_rich_text(driver, name, value, field)
        elif ftype == "select":
            ok = _fill_select(driver, name, value)
        elif ftype in ("checkbox", "radio"):
            ok = _fill_check_radio(driver, name, value)
        elif ftype == "textarea":
            ok = _fill_textarea(driver, name, value)
        else:
            ok = _fill_text(driver, name, value)

        if ok:
            filled += 1
        else:
            failed += 1

    print(f"  ✔ Form filled ({filled} ok, {failed} failed)")
    return failed == 0


def _submit_form(driver):
    wait = WebDriverWait(driver, 10)
    buttons = driver.find_elements(By.XPATH, "//button|//input[@type='submit']")
    for btn in buttons:
        txt = _norm(btn.text or btn.get_attribute("value"))
        if any(k in txt for k in ("save", "submit", "create", "update")) and "cancel" not in txt:
            try:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(2)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                return True
            except Exception:
                continue
    return False


def run(dept_code=None, driver=None):
    from config import DEPARTMENTS, set_department

    if not dept_code:
        dept_code = _pick_department()

    set_department(dept_code)
    dept_name = DEPARTMENTS.get(dept_code, dept_code)
    print(f"\n✔ Department: {dept_code} — {dept_name}")
    print(f"  Source URL: {config.SOURCE_URL}")
    dept_sort_order = _ask_sort_order()
    checkpoint_keys = _load_checkpoint_keys(dept_code)
    suggested_start_sort = max(1, len(checkpoint_keys) + 1)
    start_sort_order = _ask_event_start_sort_order(suggested_start_sort)

    own_driver = driver is None
    if own_driver:
        driver = get_driver()
    try:
        if own_driver:
            login(driver)

        edit_url = _find_department_edit_url(driver, dept_sort_order)
        if not edit_url:
            print(f"  ⚠ Could not auto-find edit page for sort order {dept_sort_order}.")
            edit_url = input("  Paste department edit URL manually (or press ENTER to stop): ").strip()
            if not edit_url:
                raise RuntimeError(
                    f"Could not find department edit page for sort order {dept_sort_order}."
                )

        driver.get(edit_url)
        events_list_url = _click_cocurricular_events(driver)
        if not events_list_url:
            raise RuntimeError("Could not open 'Department Cocurricular Events' page from edit view.")

        events = _scrape_source_events()
        if not events:
            print("✔ No co-curricular events found on source website.")
            return

        existing_title_keys = _read_existing_titles_from_list(driver, events_list_url)
        skip_keys = existing_title_keys | checkpoint_keys
        pending = [e for e in events if _title_key(e.get("title")) not in skip_keys]
        print(f"✔ Pending to create: {len(pending)} / {len(events)}")
        if not pending:
            print("✔ All source events already present in CMS.")
            return

        # Download/compress images only for records that are actually pending.
        pending = _download_images(pending)

        schema = None
        auto_submit_remaining = False
        manual_submitted_count = 0
        skipped_events = []
        print(f"  ℹ Manual interrupt: press Ctrl+C anytime, or create file: {INTERRUPT_FILE}")
        for i, event in enumerate(pending, 1):
            if os.path.exists(INTERRUPT_FILE):
                print(f"\n⏹ Stop signal found ({INTERRUPT_FILE}). Stopping gracefully.")
                break

            print("\n" + "-" * 60)
            print(f"[{i}/{len(pending)}] {event['title']}")
            print(f"Category: {event.get('category_label') or _humanize_category(event.get('category_type'))}")
            print("-" * 60)

            driver.get(events_list_url)
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(1)

            if not _open_create_form(driver):
                print("  ✘ Could not open create form, skipping record.")
                if auto_submit_remaining:
                    _append_auto_failure(dept_code, event, "Could not open create form")
                skipped_events.append(event)
                _append_skip_log(dept_code, event, reason="open_create_failed")
                continue

            if schema is None:
                schema = inspect_form(driver, url=driver.current_url)
                print(f"  ✔ Event form schema loaded ({schema['total_fields']} fields)")

            event_sort_order = start_sort_order + (i - 1)
            _fill_event_form(driver, schema, event, event_sort_order)
            if auto_submit_remaining:
                if _submit_form(driver):
                    print("  ✔ Auto-submitted")
                    _save_checkpoint_key(dept_code, event.get("title", ""))
                else:
                    print("  ⚠ Auto-submit failed; submit manually in browser.")
                    _append_auto_failure(
                        dept_code,
                        event,
                        "Auto-submit failed after fill",
                    )
                    input("  Press ENTER only after you submit this event … ")
                    _save_checkpoint_key(dept_code, event.get("title", ""))
            else:
                print("  ⚠ Review the form and click SUBMIT manually in the browser.")
                action = input(
                    "  After review: ENTER=submitted / skip / quit: "
                ).strip().lower()
                if action == "quit":
                    print("  ⏹ Stopping by user request.")
                    break
                if action == "skip":
                    skipped_events.append(event)
                    _append_skip_log(dept_code, event, reason="user_skip_after_fill")
                    print("  ⏭ Skipped and logged.")
                    continue

                manual_submitted_count += 1
                if manual_submitted_count == 2 and (len(pending) - i) > 0:
                    ans = input(
                        "  Continue remaining submissions automatically? (yes/no): "
                    ).strip().lower()
                    auto_submit_remaining = ans in ("y", "yes")
                _save_checkpoint_key(dept_code, event.get("title", ""))
                if auto_submit_remaining:
                    print(f"  ℹ Auto mode enabled. To interrupt: Ctrl+C or create {INTERRUPT_FILE}")

        if skipped_events:
            print(f"\n⚠ Skipped events: {len(skipped_events)}")
            print(f"  Logged in: {SKIP_LOG_FILE}")
        print("\n✔ Co-curricular events migration complete.")
    except KeyboardInterrupt:
        print("\n⏹ Interrupted by user. Progress checkpoint is saved for completed submissions.")
    finally:
        if own_driver:
            input("\nPress ENTER to close browser … ")
            driver.quit()


if __name__ == "__main__":
    run()

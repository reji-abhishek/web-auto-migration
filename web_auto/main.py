"""
Web Auto – Content Migration Pipeline

Migrates department data from cce.edu.in to the CMS admin portal.
Single command — choose a department and all data is scraped + filled:
  1. Faculty    (batch fill, manual crop + submit per record)
  2. Magazines  (batch fill, manual crop + submit per record)
  3. Department (4-stage: inspect → ChatGPT plan → scrape → fill)
  4. Co-curricular events (department edit flow → events create + fill)

Usage:
    python main.py              # interactive department menu → full pipeline
    python main.py CE           # skip menu, run full pipeline for CE
    python main.py CE faculty   # run only faculty for CE
    python main.py CE magazine  # run only magazine for CE
    python main.py CE department # run only department for CE
    python main.py CE cocurricular # run only co-curricular events for CE
"""

import sys
import json


def _pick_department():
    """Show an interactive menu and return the chosen department code."""
    from config import DEPARTMENTS

    if not DEPARTMENTS:
        print("\n  ⚠ Could not fetch departments from the website.")
        code = input("  Enter department code manually (e.g. CSE): ").strip().upper()
        return code

    codes = list(DEPARTMENTS.keys())
    print("\n" + "=" * 60)
    print("  SELECT DEPARTMENT  (fetched from cce.edu.in)")
    print("=" * 60)
    for i, code in enumerate(codes, 1):
        print(f"  {i}. {code} — {DEPARTMENTS[code]}")
    print()

    while True:
        choice = input("  Enter number or code (e.g. 1 or CSE): ").strip().upper()
        if choice in DEPARTMENTS:
            return choice
        try:
            idx = int(choice)
            if 1 <= idx <= len(codes):
                return codes[idx - 1]
        except ValueError:
            pass
        print(f"  ✘ Invalid choice. Try again.")


def _run_faculty(driver):
    """Check existing → scrape → batch fill faculty."""
    from config import FACULTY_DATA_FILE

    print("\n" + "=" * 60)
    print("  FACULTY — CHECKING EXISTING CMS ENTRIES")
    print("=" * 60)
    from cms_checker import get_existing_entries
    skip_names = get_existing_entries("faculty", driver=driver)

    print("\n" + "=" * 60)
    print("  FACULTY — SCRAPING SOURCE WEBSITE")
    print("=" * 60)
    from faculty_scraper import run as faculty_scrape
    records = faculty_scrape(skip_names=skip_names)

    if records:
        print("\n" + "=" * 60)
        print("  FACULTY — BATCH FILLING CMS")
        print("=" * 60)
        from form_filler import run_batch
        run_batch("faculty", records, driver=driver)
    else:
        print("\n✔ No new faculty records to fill.")


def _run_magazine(driver):
    """Check existing → scrape → batch fill magazines."""
    from config import MAGAZINE_DATA_FILE

    print("\n" + "=" * 60)
    print("  MAGAZINE — CHECKING EXISTING CMS ENTRIES")
    print("=" * 60)
    from cms_checker import get_existing_entries
    skip_titles = get_existing_entries("magazine", driver=driver)

    print("\n" + "=" * 60)
    print("  MAGAZINE — SCRAPING SOURCE WEBSITE")
    print("=" * 60)
    from magazine_scraper import run as magazine_scrape
    records = magazine_scrape(skip_titles=skip_titles)

    if records:
        print("\n" + "=" * 60)
        print("  MAGAZINE — BATCH FILLING CMS")
        print("=" * 60)
        from form_filler import run_batch
        run_batch("magazine", records, driver=driver)
    else:
        print("\n✔ No new magazine records to fill.")


def _run_department(driver):
    """Run the 4-stage department pipeline with shared browser,
    then continue to the post-submit edit flow.

    Everything happens in the terminal — no file editing required.
    """

    # Stage 1: Inspect CMS form
    print("\n" + "=" * 60)
    print("  DEPARTMENT — STAGE 1: CMS FORM INSPECTION")
    print("=" * 60)
    from cms_inspector import run as inspect_run
    schema = inspect_run(driver=driver)

    # Stage 2: Generate ChatGPT prompt + read plan from terminal
    print("\n" + "=" * 60)
    print("  DEPARTMENT — STAGE 2: AI SCRAPE PLANNING")
    print("=" * 60)
    from ai_planner import run as plan_run
    plan = plan_run(form_schema=schema)

    # Stage 3: Scrape source pages
    print("\n" + "=" * 60)
    print("  DEPARTMENT — STAGE 3: SMART SCRAPING + FILE DOWNLOAD")
    print("=" * 60)
    from smart_scraper import run as scrape_run
    scraped = scrape_run(plan=plan, schema=schema)

    # Stage 4: Fill the CMS create form
    print("\n" + "=" * 60)
    print("  DEPARTMENT — STAGE 4: FORM FILLING + FILE UPLOAD")
    print("=" * 60)
    from form_filler import run as fill_run
    fill_run(driver=driver, schema=schema, data=scraped)
    # fill_run pauses for manual review + submit and waits for ENTER

    # Stage 5: Post-submit edit flow
    print("\n" + "=" * 60)
    print("  DEPARTMENT — STAGE 5: POST-SUBMIT EDIT PAGE")
    print("=" * 60)
    ans = input("\n  Run the edit-page flow now? (ENTER=yes / skip): ").strip().lower()
    if ans != "skip":
        from dept_edit_handler import run_edit_flow
        run_edit_flow(driver)


def main():
    args = sys.argv[1:]

    # ── Determine department ──────────────────────────────────
    dept_code = None
    run_only = None  # None = run all, or "faculty"/"magazine"/"department"/"cocurricular"

    if args:
        from config import DEPARTMENTS
        candidate = args[0].upper()
        if DEPARTMENTS and candidate not in DEPARTMENTS:
            print(f"  ✘ Unknown department code: {args[0]}")
            print(f"  Available: {', '.join(sorted(DEPARTMENTS.keys()))}")
            sys.exit(1)
        dept_code = candidate
        if len(args) > 1:
            run_only = args[1].lower()
    else:
        dept_code = _pick_department()

    # ── Set the department globally ───────────────────────────
    from config import set_department, DEPARTMENTS
    set_department(dept_code)

    import config
    dept_name = DEPARTMENTS.get(dept_code, dept_code)
    print(f"\n✔ Department: {dept_code} — {dept_name}")
    print(f"  Source URL: {config.SOURCE_URL}")

    # ── Single browser session ────────────────────────────────
    from browser import get_driver, login
    driver = get_driver()

    try:
        login(driver)

        # ── 1. FACULTY ────────────────────────────────────────
        if run_only in (None, "faculty"):
            _run_faculty(driver)

        # ── 2. MAGAZINE ───────────────────────────────────────
        if run_only in (None, "magazine"):
            _run_magazine(driver)

        # ── 3. DEPARTMENT ─────────────────────────────────────
        if run_only in (None, "department"):
            _run_department(driver)

        # ── 4. CO-CURRICULAR EVENTS ───────────────────────────
        if run_only in (None, "cocurricular", "co-curricular", "events"):
            from cocurricular_events_flow import run as cocurricular_run
            cocurricular_run(dept_code=dept_code, driver=driver)

        print("\n" + "=" * 60)
        print("  ✔ ALL DONE")
        print("=" * 60)
        input("\nPress ENTER to close browser … ")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()

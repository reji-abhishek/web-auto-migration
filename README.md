# Web Auto – Content Migration Pipeline

This project automates the migration of department data from a source college website to a CMS admin portal. It is designed for personal use to streamline scraping, planning, and form-filling tasks for structured academic content.

## Features
- Scrapes department, faculty, and magazine data from the source website
- Generates AI-assisted scrape plans using ChatGPT
- Fills CMS admin forms automatically using Selenium
- Downloads and organizes images, PDFs, and other assets
- All credentials and URLs are managed via a `.env` file for security

## Project Structure
- `main.py` – Entry point for the full migration pipeline
- `scraper.py` – Scrapes and structures department content
- `ai_planner.py` – Generates prompts and reads scrape plans from ChatGPT
- `form_filler.py` / `fill_form.py` – Automates CMS form filling
- `config.py` – Loads configuration and environment variables
- `assets/` – Downloaded files and images
- `.env` – Environment variables (not committed)
- `.gitignore` – Excludes sensitive and generated files

## Setup
1. Clone the repository.
2. Create a Python virtual environment and activate it:
   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```
4. Create a `.env` file in the project root with the following variables:
   ```env
   LOGIN_URL=...
   CREATE_PAGE=...
   DEPT_HOME=...
   EMAIL=...
   PASSWORD=...
   CMS_BASE=...
   SOURCE_DOMAIN=...
   ```
   (Fill in with your actual credentials and URLs.)

## Usage
- Run the main pipeline interactively:
  ```sh
  python main.py
  ```
- Run only department co-curricular events automation:
  ```sh
  python main.py EEE cocurricular
  ```
- To scrape and structure department data only:
  ```sh
  python scraper.py
  ```
- Follow prompts to copy/paste AI scrape plans and review output files.

## Co-curricular Events Automation
- Added `web_auto/cocurricular_events_flow.py`.
- It opens the chosen department edit page in CMS, navigates to **Department Cocurricular Events**, scrapes source events from `SOURCE_URL/events`, and auto-creates missing event entries.

## Security
- All sensitive information is stored in `.env` and excluded from version control.
- Do not share your `.env` file or push it to public repositories.

## License
This project is for personal/educational use. No warranty is provided.

"""
Shared Selenium browser utilities: driver creation and login.
"""

import shutil
import time
import re

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import LOGIN_URL, EMAIL, PASSWORD


def get_driver():
    """Create and return a configured Chrome WebDriver."""
    chrome_path = (
        shutil.which("chromium-browser")
        or shutil.which("chromium")
        or shutil.which("google-chrome")
    )
    options = Options()
    if chrome_path:
        options.binary_location = chrome_path
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def login(driver):
    """Log in to the CMS admin portal. Returns the driver."""
    if not LOGIN_URL or not re.match(r"^https?://", LOGIN_URL):
        raise RuntimeError(
            "Invalid LOGIN_URL/CMS_BASE configuration. "
            "Set either CMS_BASE or LOGIN_URL in web_auto/.env."
        )
    if not EMAIL or not PASSWORD:
        raise RuntimeError(
            "Missing EMAIL/PASSWORD in web_auto/.env."
        )

    wait = WebDriverWait(driver, 30)
    driver.get(LOGIN_URL)

    email_field = wait.until(
        EC.presence_of_element_located((By.NAME, "email"))
    )
    password_field = driver.find_element(By.NAME, "password")

    email_field.send_keys(EMAIL)
    password_field.send_keys(PASSWORD)

    driver.find_element(By.XPATH, "//button[contains(.,'Login')]").click()

    wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//a[contains(.,'Dashboard')]")
        )
    )
    print("✔ Logged in")
    return driver

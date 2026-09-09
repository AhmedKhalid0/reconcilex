"""
Automated Clean Screenshot Capturer for ReconcileX.
Captures pristine, high-resolution screenshots without any agent highlight frames or blue borders.
"""

import os
from pathlib import Path
import time
from playwright.sync_api import sync_playwright

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
if not os.path.exists(CHROME_PATH):
    CHROME_PATH = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"


def capture_all():
    print(f"Launching clean browser via: {CHROME_PATH}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 920},
            device_scale_factor=2,  # Retina crispness
        )
        page = context.new_page()

        print("Navigating to http://127.0.0.1:8585/...")
        page.goto("http://127.0.0.1:8585/", wait_until="networkidle")
        time.sleep(1)

        # Ensure light mode initially
        page.evaluate("localStorage.setItem('rx-theme', 'light'); document.documentElement.setAttribute('data-theme', 'light');")

        # 1. Load Demo Dataset
        print("Clicking 'Load Demo Dataset'...")
        page.click("button:has-text('Load Demo Dataset')")
        page.wait_for_timeout(2500)

        # Screenshot 1: Dashboard Overview (KPIs, Charts, Matches Table)
        shot1 = SCREENSHOTS_DIR / "01_dashboard_kpis_and_analytics.png"
        page.screenshot(path=str(shot1))
        print(f"Captured: {shot1.name}")

        # Screenshot 2: Interactive Split Ledger with Floating Dock
        print("Navigating to Interactive Split-Ledger...")
        page.click("button:has-text('Interactive Split-Ledger')")
        page.wait_for_timeout(600)

        # Select items to show floating dock
        inv_items = page.locator("#split-inv-list .ledger-item")
        tx_items = page.locator("#split-tx-list .ledger-item")
        if inv_items.count() > 0:
            inv_items.first.click()
        if tx_items.count() > 0:
            tx_items.first.click()

        page.wait_for_timeout(600)
        shot2 = SCREENSHOTS_DIR / "02_interactive_split_ledger_dock.png"
        page.screenshot(path=str(shot2))
        print(f"Captured: {shot2.name}")

        # Screenshot 3: Slide-Out Forensic Document Inspector Drawer
        print("Opening Forensic Document Inspector Drawer for INV-2026-003...")
        page.evaluate("inspectDocument('INV-2026-003')")
        page.wait_for_timeout(1000)
        shot3 = SCREENSHOTS_DIR / "03_forensic_document_inspector_drawer.png"
        page.screenshot(path=str(shot3))
        print(f"Captured: {shot3.name}")

        # Close drawer and clear selections
        page.evaluate("closeDrawer()")
        page.evaluate("clearSplitSelections()")
        page.wait_for_timeout(400)

        # Screenshot 4: Tax & ZATCA Statutory Compliance Audit Tab
        print("Navigating to Tax & ZATCA Audit tab...")
        page.click("button:has-text('Tax & ZATCA Audit')")
        page.wait_for_timeout(1500)
        shot4 = SCREENSHOTS_DIR / "04_tax_and_zatca_compliance_audit.png"
        page.screenshot(path=str(shot4))
        print(f"Captured: {shot4.name}")

        # Screenshot 5: Parameter Tuning Studio Tab
        print("Navigating to Parameter Studio tab...")
        page.click("button:has-text('Parameter Studio')")
        page.wait_for_timeout(600)
        shot5 = SCREENSHOTS_DIR / "05_rules_and_parameter_studio.png"
        page.screenshot(path=str(shot5))
        print(f"Captured: {shot5.name}")

        # Screenshot 6: Eye-Comfort Dark Mode (Matched Pairs overview)
        print("Switching to Dark Mode on Matched Pairs tab...")
        page.click("button:has-text('Matched Pairs')")
        page.click("#theme-btn")
        page.wait_for_timeout(600)
        shot6 = SCREENSHOTS_DIR / "06_dark_mode_ergonomics.png"
        page.screenshot(path=str(shot6))
        print(f"Captured: {shot6.name}")

        browser.close()
        print("All clean screenshots successfully regenerated!")


if __name__ == "__main__":
    capture_all()

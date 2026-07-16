#!/usr/bin/env python3
"""
Bulk-delete ghcr.io packages under a user namespace via browser automation.

Why this exists: the GitHub API delete call failed with a 403 scope error
(delete:packages missing from the token), and reissuing a token wasn't done.
This drives the same UI you'd use manually - same approach as
make_public_browser.py, reusing the proven dialog-scoping technique.

Usage:
    # Step 1 (skip if gh_auth_state.json from make_public_browser.py already exists
    # and is still a valid session - this script reuses the same file):
    python3 delete_packages_browser.py --login

    # Step 2: dry run - just lists what would be deleted, deletes nothing
    python3 delete_packages_browser.py --owner man4ish

    # Step 3: actually delete (requires typing DELETE to confirm once, up front)
    python3 delete_packages_browser.py --owner man4ish --confirm-delete

Reads package names from ./old_packages.txt (produced by verify_migration.sh).
Safe to re-run / resume: progress logged to ./delete_browser.log.
"""

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("Missing dependency. Run: pip install playwright --break-system-packages")
    print("Then:   python3 -m playwright install chromium")
    sys.exit(1)

AUTH_STATE_FILE = "gh_auth_state.json"
LOG_FILE = "delete_browser.log"
PACKAGES_FILE = "old_packages.txt"


def load_done_log():
    p = Path(LOG_FILE)
    if not p.exists():
        return set()
    return set(line.strip() for line in p.read_text().splitlines() if line.strip())


def mark_done(name):
    with open(LOG_FILE, "a") as f:
        f.write(name + "\n")


def do_login():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://github.com/login")
        print("A browser window has opened. Log into GitHub manually (including 2FA if prompted).")
        print("Once you're logged in and see your GitHub dashboard, come back here and press Enter.")
        input()
        context.storage_state(path=AUTH_STATE_FILE)
        browser.close()
        print(f"Session saved to {AUTH_STATE_FILE}. You can now run without --login.")


def dump_debug_info(page, pkg_name):
    safe_name = pkg_name.replace("/", "_")
    screenshot_path = f"debug_delete_{safe_name}.png"
    try:
        page.screenshot(path=screenshot_path, full_page=True)
    except Exception as e:
        screenshot_path = f"(failed to capture: {e})"

    print(f"\n  --- DEBUG for {pkg_name} ---")
    print(f"  URL: {page.url}")
    print(f"  Page title: {page.title()}")
    print(f"  Screenshot saved: {screenshot_path}")

    print("  Visible buttons on page:")
    try:
        for b in page.get_by_role("button").all_text_contents():
            b = b.strip()
            if b:
                print(f"    - {b!r}")
    except Exception as e:
        print(f"    (could not list buttons: {e})")
    print("  --- end debug ---\n")


def find_open_dialog(page, text_hint):
    """Same technique as make_public_browser.py - GitHub's page always has a
    hidden search dialog with role=dialog, so filter by content not just role."""
    candidates = [
        page.locator('[role="dialog"]').filter(has_text=text_hint),
        page.locator('.Overlay').filter(has_text=text_hint),
        page.locator('details-dialog').filter(has_text=text_hint),
        page.locator('dialog').filter(has_text=text_hint),
    ]
    for c in candidates:
        try:
            if c.count() > 0 and c.first.is_visible():
                return c.first
        except Exception:
            continue
    return None


def delete_package(page, owner, pkg_name):
    encoded_name = quote(pkg_name, safe="")
    url = f"https://github.com/users/{owner}/packages/container/{encoded_name}/settings"
    page.goto(url, wait_until="domcontentloaded")

    if "Page not found" in page.title():
        return "page_not_found"  # already deleted, or never existed - treat as done

    # GitHub sometimes redirects away to the general packages listing instead
    # of a 404 when the package doesn't exist at this exact name/path.
    if "/settings" not in page.url or page.title().strip() == "Your Packages":
        return "page_not_found"

    try:
        page.get_by_role("button", name="Delete this package").click(timeout=10000)
    except PWTimeout:
        dump_debug_info(page, pkg_name)
        return "no_delete_button"

    page.wait_for_timeout(500)

    dialog = find_open_dialog(page, "Delete this package?")
    if dialog is None:
        dump_debug_info(page, pkg_name)
        return "no_dialog"

    # Fill "type NAME to confirm" textbox, scoped to the dialog
    try:
        dialog.get_by_role("textbox").first.fill(pkg_name, timeout=5000)
    except PWTimeout:
        try:
            page.locator('input[type="text"]:visible').last.fill(pkg_name, timeout=5000)
        except PWTimeout:
            dump_debug_info(page, pkg_name)
            return "no_confirm_textbox"

    submit_name = "I understand the consequences, delete this package"
    try:
        dialog.get_by_role("button", name=submit_name, exact=True).click(timeout=5000)
    except PWTimeout:
        try:
            page.get_by_role("button", name=submit_name, exact=True).click(timeout=5000)
        except PWTimeout:
            dump_debug_info(page, pkg_name)
            return "submit_button_not_found"

    page.wait_for_timeout(1500)
    return "deleted"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Interactively log in and save session")
    parser.add_argument("--owner", required=False, default="man4ish", help="GitHub username that owns the packages")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between packages")
    parser.add_argument("--continue-on-fail", action="store_true",
                         help="Keep going after a failure instead of stopping at the first one for debugging")
    parser.add_argument("--confirm-delete", action="store_true",
                         help="Actually perform deletions. Without this flag, only a dry-run list is printed.")
    parser.add_argument("--headless", action="store_true",
                         help="Run without a visible browser window (needed if no X display is available)")
    args = parser.parse_args()

    if args.login:
        do_login()
        return

    if not Path(AUTH_STATE_FILE).exists():
        print(f"No saved session found ({AUTH_STATE_FILE}). Run with --login first.")
        sys.exit(1)

    pkg_file = Path(PACKAGES_FILE)
    if not pkg_file.exists():
        print(f"!! {PACKAGES_FILE} not found. Run verify_migration.sh first to generate it.")
        sys.exit(1)

    all_packages = [l.strip() for l in pkg_file.read_text().splitlines() if l.strip()]
    done = load_done_log()
    todo = [n for n in all_packages if n not in done]

    print(f"{len(all_packages)} packages listed in {PACKAGES_FILE}, {len(todo)} remaining to process.")

    if not args.confirm_delete:
        print("\n=== DRY RUN MODE (default) ===")
        print("No packages will be deleted. Packages that WOULD be deleted:\n")
        for n in todo:
            print(f"  [would delete] ghcr.io/{args.owner}/{n}")
        print(f"\nTo actually delete, re-run with --confirm-delete")
        return

    print("\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    print(f"!! THIS WILL PERMANENTLY DELETE {len(todo)} PACKAGES. THIS CANNOT BE UNDONE. !!")
    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n")
    print("Type DELETE (all caps) to proceed, anything else to abort:")
    confirmation = input()
    if confirmation != "DELETE":
        print("Aborted. Nothing was deleted.")
        return

    counts = {"deleted": 0, "page_not_found": 0, "failed": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(storage_state=AUTH_STATE_FILE)
        page = context.new_page()

        for i, name in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {name} ...", end=" ", flush=True)
            result = None
            for attempt in range(3):
                try:
                    result = delete_package(page, args.owner, name)
                    break
                except Exception as e:
                    err_str = str(e)
                    transient = any(x in err_str for x in [
                        "net::ERR_NETWORK_CHANGED", "net::ERR_CONNECTION",
                        "net::ERR_INTERNET_DISCONNECTED", "net::ERR_TIMED_OUT",
                    ])
                    if transient and attempt < 2:
                        wait_s = 3 * (attempt + 1)
                        print(f"(network hiccup, retrying in {wait_s}s) ", end="", flush=True)
                        time.sleep(wait_s)
                        continue
                    result = f"error: {e}"
                    break

            print(result)

            if result in ("deleted", "page_not_found"):
                mark_done(name)
                counts[result if result in counts else "deleted"] = counts.get(result, 0) + 1
            else:
                counts["failed"] += 1
                if not args.continue_on_fail:
                    print("\nStopping at first failure for debugging (see debug_*.png and output above).")
                    print("Share that output, or re-run with --continue-on-fail to push through.")
                    break

            time.sleep(args.delay)

        browser.close()

    print("\n==== Summary ====")
    for k, v in counts.items():
        print(f"{k}: {v}")
    print(f"\nRe-run the same command to retry anything that failed — completed packages are skipped via {LOG_FILE}.")


if __name__ == "__main__":
    main()
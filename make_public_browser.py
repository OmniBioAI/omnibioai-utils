#!/usr/bin/env python3
"""
Bulk-set ghcr.io package visibility to public for an org, via browser automation.

Why this exists: GitHub's REST API has no endpoint to update package visibility
(confirmed against current docs — only get/list/delete/restore exist). The only
supported way is through the web UI, so this drives that UI with Playwright.

Usage:
    pip install playwright --break-system-packages
    python3 -m playwright install chromium

    # Step 1: log in once (opens a real browser window, you log in manually,
    # including 2FA if needed). Session is saved to ./gh_auth_state.json
    python3 make_public_browser.py --login

    # Step 2: run the bulk visibility change. Uses org_packages.txt if present
    # (produced by set_packages_public.sh) to skip already-public packages;
    # otherwise pass --org and it will discover packages by visiting the org
    # packages page.
    python3 make_public_browser.py --org omnibioai

Safe to re-run / resume: progress is logged to ./visibility_browser.log and
already-completed packages are skipped on subsequent runs.
"""

import argparse
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("Missing dependency. Run: pip install playwright --break-system-packages")
    print("Then:   python3 -m playwright install chromium")
    sys.exit(1)

AUTH_STATE_FILE = "gh_auth_state.json"
LOG_FILE = "visibility_browser.log"
ORG_PACKAGES_FILE = "org_packages.txt"   # tab-separated name\tvisibility, from set_packages_public.sh

# Packages that should always stay private - never touched by this script,
# even if org_packages.txt lists them as non-public.
ALWAYS_PRIVATE = {
    "omnibioai-dev-hub",
    "omnibioai-tes",
    "omnibioai-app",
    "omnibioai-lims",
    "omnibioai-license-server",
}


def load_done_log():
    p = Path(LOG_FILE)
    if not p.exists():
        return set()
    return set(line.strip() for line in p.read_text().splitlines() if line.strip())


def mark_done(name):
    with open(LOG_FILE, "a") as f:
        f.write(name + "\n")


def load_candidate_packages(org):
    """Return list of package names that are NOT already public."""
    p = Path(ORG_PACKAGES_FILE)
    if p.exists():
        names = []
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            name, visibility = parts
            if visibility != "public" and name not in ALWAYS_PRIVATE:
                names.append(name)
        return names
    else:
        print(f"!! {ORG_PACKAGES_FILE} not found. Run set_packages_public.sh first to generate it")
        print("   (it lists all packages + current visibility via the API, which still works fine).")
        sys.exit(1)


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
    screenshot_path = f"debug_{safe_name}.png"
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
        buttons = page.get_by_role("button").all_text_contents()
        for b in buttons:
            b = b.strip()
            if b:
                print(f"    - {b!r}")
    except Exception as e:
        print(f"    (could not list buttons: {e})")

    print("  Visible headings on page:")
    try:
        headings = page.locator("h1, h2, h3").all_text_contents()
        for h in headings:
            h = h.strip()
            if h:
                print(f"    - {h!r}")
    except Exception as e:
        print(f"    (could not list headings: {e})")

    print("  Radio inputs on page:")
    try:
        radios = page.get_by_role("radio").all()
        for r in radios:
            try:
                name = r.get_attribute("aria-label") or r.get_attribute("value") or r.get_attribute("id")
                print(f"    - radio: {name!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"    (could not list radios: {e})")

    print("  Open dialog content (if any):")
    try:
        dialogs = page.locator('[role="dialog"]').all()
        if not dialogs:
            print("    (no [role=dialog] element found)")
        for d in dialogs:
            text = d.inner_text().strip()
            print(f"    --- dialog text ---\n{text}\n    --- end dialog text ---")
    except Exception as e:
        print(f"    (could not read dialog: {e})")
    print("  --- end debug ---\n")


def find_open_dialog(page, text_hint):
    """GitHub's page always has a hidden search dialog with role=dialog sitting
    in the DOM, so we must filter by content, not just role, to find the real
    confirmation modal. Try a few possible container patterns."""
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


def set_package_visibility(page, org, pkg_name, target="public"):
    from urllib.parse import quote
    encoded_name = quote(pkg_name, safe="")
    url = f"https://github.com/orgs/{org}/packages/container/{encoded_name}/settings"
    page.goto(url, wait_until="domcontentloaded")

    if "Page not found" in page.title():
        return "page_not_found"

    already_text = f"This package is currently {target}"
    if page.get_by_text(already_text, exact=False).count() > 0:
        return f"already_{target}"

    # Click "Change visibility" in the Danger Zone
    try:
        page.get_by_role("button", name="Change visibility").click(timeout=10000)
    except PWTimeout:
        dump_debug_info(page, pkg_name)
        return "no_change_button"

    page.wait_for_timeout(500)  # let the modal animate in

    dialog = find_open_dialog(page, "Change package visibility")
    if dialog is None:
        dump_debug_info(page, pkg_name)
        return "no_dialog"

    # Select the target radio, scoped to the dialog.
    # Using the value attribute directly rather than accessible name, since
    # GitHub's label isn't reliably associated for accessible-name lookup.
    try:
        dialog.locator(f'input[type="radio"][value="{target}"]').check(timeout=5000)
    except PWTimeout:
        try:
            # fallback: some package variants (e.g. repo-linked packages)
            # render the radio outside the scoped dialog container
            page.locator(f'input[type="radio"][value="{target}"]').first.check(timeout=5000)
        except PWTimeout:
            dump_debug_info(page, pkg_name)
            return f"no_{target}_radio"

    # Fill the "type NAME to confirm" textbox, scoped to the dialog
    try:
        dialog.get_by_role("textbox").first.fill(pkg_name, timeout=5000)
    except PWTimeout:
        try:
            page.locator('input[type="text"]:visible').last.fill(pkg_name, timeout=5000)
        except PWTimeout:
            dump_debug_info(page, pkg_name)
            return "no_confirm_textbox"

    # Submit
    submit_name = "I understand the consequences, change package visibility"
    try:
        dialog.get_by_role("button", name=submit_name, exact=True).click(timeout=5000)
    except PWTimeout:
        try:
            page.get_by_role("button", name=submit_name, exact=True).click(timeout=5000)
        except PWTimeout:
            dump_debug_info(page, pkg_name)
            return "submit_button_not_found"

    page.wait_for_timeout(1500)
    return "changed"


# Backwards-compatible name used elsewhere in this file
def set_package_public(page, org, pkg_name):
    return set_package_visibility(page, org, pkg_name, target="public")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Interactively log in and save session")
    parser.add_argument("--org", default="omnibioai", help="GitHub org name")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between packages")
    parser.add_argument("--continue-on-fail", action="store_true",
                         help="Keep going after a failure instead of stopping at the first one for debugging")
    parser.add_argument("--revert-always-private", action="store_true",
                         help="Instead of the normal run, set every package in ALWAYS_PRIVATE back to private "
                              "(use this once, to undo any that were already made public before the skip list existed)")
    args = parser.parse_args()

    if args.login:
        do_login()
        return

    if not Path(AUTH_STATE_FILE).exists():
        print(f"No saved session found ({AUTH_STATE_FILE}). Run with --login first.")
        sys.exit(1)

    if args.revert_always_private:
        print(f"Reverting {len(ALWAYS_PRIVATE)} packages to private: {', '.join(sorted(ALWAYS_PRIVATE))}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(storage_state=AUTH_STATE_FILE)
            page = context.new_page()
            for name in sorted(ALWAYS_PRIVATE):
                print(f"{name} ...", end=" ", flush=True)
                try:
                    result = set_package_visibility(page, args.org, name, target="private")
                except Exception as e:
                    result = f"error: {e}"
                print(result)
                time.sleep(args.delay)
            browser.close()
        return

    candidates = load_candidate_packages(args.org)
    excluded_present = [n for n in ALWAYS_PRIVATE]
    print(f"Excluding {len(excluded_present)} packages permanently marked private: {', '.join(sorted(excluded_present))}")
    done = load_done_log()
    todo = [n for n in candidates if n not in done]

    print(f"{len(candidates)} private/non-public packages found, {len(todo)} remaining to process.")

    counts = {"changed": 0, "already_public": 0, "failed": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=AUTH_STATE_FILE)
        page = context.new_page()

        for i, name in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {name} ...", end=" ", flush=True)
            result = None
            for attempt in range(3):
                try:
                    result = set_package_public(page, args.org, name)
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

            if result in ("changed", "already_public"):
                mark_done(name)
                counts[result if result in counts else "changed"] = counts.get(result, 0) + 1
            else:
                counts["failed"] += 1
                if not args.continue_on_fail:
                    print("\nStopping at first failure for debugging (see debug_*.png and output above).")
                    print("Share that output, or re-run with --continue-on-fail to push through and collect all failures.")
                    break

            time.sleep(args.delay)

        browser.close()

    print("\n==== Summary ====")
    for k, v in counts.items():
        print(f"{k}: {v}")
    print(f"\nRe-run the same command to retry anything that failed — completed packages are skipped via {LOG_FILE}.")


if __name__ == "__main__":
    main()
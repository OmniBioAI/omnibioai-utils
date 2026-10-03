#!/usr/bin/env python3
"""
Bulk-grant a repository a role (read/write/admin) on ghcr.io packages, via
browser automation -- the companion to make_public_browser.py.

Why this exists: the SIF packages (ghcr.io/omnibioai/omnibioai-sif/<name>)
are linked to their source repository, omnibioai-tool-images, through the
org.opencontainers.image.source label, but that repository only gets Read in
the package's "Manage Actions access" list. GitHub's REST API has no endpoint
for a package's "Manage Actions access" list, so -- as with visibility -- the
only way is the web UI, driven here with Playwright.

Usage:
    # Reuses make_public_browser.py's saved session (gh_auth_state.json);
    # log in once if you have not already:
    python3 make_public_browser.py --login

    # Check against a package you already set by hand (expect "already_write"):
    python3 grant_package_access_browser.py --only omnibioai-sif/bedtools

    # Then every omnibioai-sif/* package. Names are listed via the API when
    # GH_TOKEN (read:packages) is set, else read from org_packages.txt
    # (written by set_packages_public.sh):
    GH_TOKEN=... python3 grant_package_access_browser.py

Safe to re-run / resume: completed packages are logged to ./access_browser.log
and skipped on later runs. Stops at the first failure with a debug dump unless
--continue-on-fail is given.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from urllib.parse import quote

from make_public_browser import (
    AUTH_STATE_FILE,
    PWTimeout,
    do_login,
    dump_debug_info,
    sync_playwright,
)

LOG_FILE = "access_browser.log"
ORG_PACKAGES_FILE = "org_packages.txt"   # name\tvisibility, from set_packages_public.sh
ROLES = ("read", "write", "admin")


def load_done_log():
    if not os.path.exists(LOG_FILE):
        return set()
    with open(LOG_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def mark_done(name):
    with open(LOG_FILE, "a") as f:
        f.write(name + "\n")


def list_packages(org, prefix, token):
    """Return names of the org's container packages that start with prefix."""
    names, page_no = [], 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/orgs/{org}/packages?package_type=container&per_page=100&page={page_no}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req) as resp:
            batch = json.load(resp)
        if not batch:
            return names
        names += [p["name"] for p in batch if p["name"].startswith(prefix)]
        page_no += 1


def load_packages_file(prefix):
    """Names from set_packages_public.sh's org_packages.txt that start with prefix."""
    with open(ORG_PACKAGES_FILE) as f:
        names = [line.split("\t")[0].strip() for line in f]
    return [n for n in names if n.startswith(prefix)]


def repo_pattern(repo):
    # Match "repo" or "OmniBioAI/repo", but not a longer name or path that
    # merely starts with it (e.g. a package name "repo/bedtools").
    return re.compile(rf"(?<![\w-]){re.escape(repo)}(?![\w/-])", re.I)


def find_repo_row(page, repo):
    """The innermost element naming the repository that also holds its role
    control ("Role: Write" button or a <select>). Requiring the role control
    skips the "Repository source" box, which names the same repository."""
    role_control = page.locator("select").or_(page.get_by_role("button", name=re.compile(r"^Role", re.I)))
    rows = (page.locator("li, tr, .Box-row, div")
            .filter(has_text=repo_pattern(repo))
            .filter(has=role_control))
    return rows.last if rows.count() > 0 else None


def find_visible_dialog(page, text_hint):
    """First *visible* dialog containing text_hint. find_open_dialog() only
    checks the first match per selector, and the package settings page keeps
    several hidden dialogs in the DOM ahead of the one that opens."""
    for selector in ('[role="dialog"]', "dialog", ".Overlay"):
        matches = page.locator(selector).filter(has_text=text_hint)
        for i in range(matches.count()):
            if matches.nth(i).is_visible():
                return matches.nth(i)
    return None


def add_repository(page, repo):
    """Add repo to the package's Actions access list. Returns an error code or None."""
    try:
        page.get_by_role("button", name=re.compile(r"Add repository", re.I)).first.click(timeout=10000)
    except PWTimeout:
        return "no_add_repository_button"
    page.wait_for_timeout(500)

    dialog = find_visible_dialog(page, "Add repositor")
    if dialog is None:
        return "no_dialog"
    try:
        dialog.get_by_placeholder(re.compile(r"Filter repositor", re.I)).first.fill(repo, timeout=5000)
    except PWTimeout:
        return "no_search_box"
    page.wait_for_timeout(1500)  # search results load asynchronously

    confirm = tick_repository(page, dialog, repo)
    if confirm is None:
        return "repo_not_selectable"
    try:
        confirm.click(timeout=5000)
    except PWTimeout:
        return "no_add_confirm_button"
    page.wait_for_timeout(1500)
    return None


def tick_repository(page, dialog, repo):
    """Select repo in the picker's results. "Add repositories" stays disabled
    until something is ticked, so try each way the result row may be rendered
    and stop once that button is enabled. Returns the button, or None."""
    pattern = repo_pattern(repo)
    confirm = dialog.get_by_role("button", name=re.compile(r"^Add repositor", re.I)).last
    attempts = (
        lambda: dialog.get_by_role("checkbox", name=pattern).first.check(timeout=3000),
        lambda: (dialog.locator("li, label").filter(has_text=pattern).last
                 .locator('input[type="checkbox"]').first.check(timeout=3000)),
        lambda: dialog.get_by_role("option", name=pattern).first.click(timeout=3000),
        lambda: dialog.get_by_text(pattern).first.click(timeout=3000),
    )
    for attempt in attempts:
        try:
            attempt()
        except Exception:  # timeout, or "click did not change its state"
            continue
        page.wait_for_timeout(300)
        if confirm.is_enabled():
            return confirm
    return None


def set_row_role(page, row, role):
    """Set the role in a repository row; GitHub saves the change immediately."""
    label = role.capitalize()
    select = row.locator("select")
    if select.count() > 0:
        if select.first.input_value().lower() == role:
            return f"already_{role}"
        try:
            select.first.select_option(label=label, timeout=5000)
        except PWTimeout:
            return "role_select_failed"
    else:
        try:
            button = row.get_by_role("button", name=re.compile(r"role|read|write|admin", re.I)).first
            if label in button.inner_text(timeout=5000):
                return f"already_{role}"
            button.click(timeout=5000)
            page.locator('[role^="menuitem"]').filter(has_text=label).first.click(timeout=5000)
        except PWTimeout:
            return "no_role_control"
    page.wait_for_timeout(1500)
    return "changed"


def grant_repo_access(page, org, pkg_name, repo, role="write"):
    url = f"https://github.com/orgs/{org}/packages/container/{quote(pkg_name, safe='')}/settings"
    page.goto(url, wait_until="domcontentloaded")
    if "Page not found" in page.title():
        return "page_not_found"

    row = find_repo_row(page, repo)
    if row is None:
        error = add_repository(page, repo)
        if error is None:
            row = find_repo_row(page, repo)
            error = None if row is not None else "repo_row_missing"
        if error:
            dump_debug_info(page, pkg_name)
            return error

    result = set_row_role(page, row, role)
    if result not in ("changed", f"already_{role}"):
        dump_debug_info(page, pkg_name)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Interactively log in and save session")
    parser.add_argument("--org", default="omnibioai", help="GitHub org name")
    parser.add_argument("--repo", default="omnibioai-tool-images", help="Repository to grant access to")
    parser.add_argument("--role", default="write", choices=ROLES, help="Role to grant")
    parser.add_argument("--prefix", default="omnibioai-sif/", help="Only packages whose name starts with this")
    parser.add_argument("--only", action="append", default=[], metavar="PACKAGE",
                        help="Process just this package (repeatable); skips the API listing")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between packages")
    parser.add_argument("--continue-on-fail", action="store_true",
                        help="Keep going after a failure instead of stopping at the first one for debugging")
    args = parser.parse_args()

    if args.login:
        do_login()
        return

    if not os.path.exists(AUTH_STATE_FILE):
        print(f"No saved session found ({AUTH_STATE_FILE}). Run with --login first.")
        sys.exit(1)

    if args.only:
        todo = args.only
    else:
        token = os.environ.get("GH_TOKEN")
        if token:
            packages = list_packages(args.org, args.prefix, token)
        elif os.path.exists(ORG_PACKAGES_FILE):
            print(f"GH_TOKEN not set; using {ORG_PACKAGES_FILE} (may be stale).")
            packages = load_packages_file(args.prefix)
        else:
            print(f"Set GH_TOKEN (read:packages) to list packages, provide {ORG_PACKAGES_FILE}, "
                  "or pass --only NAME.")
            sys.exit(1)
        done = load_done_log()
        todo = [n for n in packages if n not in done]
        print(f"{len(packages)} packages match '{args.prefix}', {len(todo)} remaining to process.")

    ok = (f"already_{args.role}", "changed")
    counts = {"changed": 0, f"already_{args.role}": 0, "failed": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=AUTH_STATE_FILE)
        page = context.new_page()

        for i, name in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {name} ...", end=" ", flush=True)
            try:
                result = grant_repo_access(page, args.org, name, args.repo, args.role)
            except Exception as e:
                result = f"error: {e}"
            print(result)

            if result in ok:
                mark_done(name)
                counts[result] += 1
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
    print(f"\nRe-run the same command to retry failures -- completed packages are skipped via {LOG_FILE}.")


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()

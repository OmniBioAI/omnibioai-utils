"""Tests for grant_package_access_browser.py: package listing, the per-step
browser helpers, and the CLI, with Playwright and the GitHub API stubbed
via MagicMock/monkeypatch.

Developer: Manish Kumar <manish@omnibioai.org>
"""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grant_package_access_browser as mod  # noqa: E402

TIMEOUT = mod.PWTimeout("timeout")


def test_done_log_and_packages_file(monkeypatch, tmp_path):
    """Track completed packages and read prefix-matching names from
    set_packages_public.sh's org_packages.txt."""
    monkeypatch.setattr(mod, "LOG_FILE", str(tmp_path / "done.log"))
    assert mod.load_done_log() == set()
    mod.mark_done("omnibioai-sif/a")
    assert mod.load_done_log() == {"omnibioai-sif/a"}

    monkeypatch.setattr(mod, "ORG_PACKAGES_FILE", str(tmp_path / "pkgs.txt"))
    (tmp_path / "pkgs.txt").write_text("omnibioai-sif/a\tprivate\nomnibioai-app\tprivate\nomnibioai-sif/b\tpublic\n")
    assert mod.load_packages_file("omnibioai-sif/") == ["omnibioai-sif/a", "omnibioai-sif/b"]


def test_list_packages_paginates_and_filters(monkeypatch):
    """Page through the org packages API until an empty page, keeping only
    names under the prefix."""
    pages = [[{"name": "omnibioai-sif/a"}, {"name": "omnibioai-app"}], [{"name": "omnibioai-sif/b"}], []]
    seen = []

    def urlopen(req):
        seen.append(req.full_url)
        return io.BytesIO(json.dumps(pages[len(seen) - 1]).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    assert mod.list_packages("omnibioai", "omnibioai-sif/", "tok") == ["omnibioai-sif/a", "omnibioai-sif/b"]
    assert seen[-1].endswith("page=3")


def test_repo_pattern_skips_package_name():
    """Match the repository name, alone or org-qualified, but not the package
    path that also starts with it."""
    pat = mod.repo_pattern("omnibioai-sif")
    assert pat.search("OmniBioAI/omnibioai-sif  Write")
    assert pat.search("omnibioai-sif")
    assert not pat.search("omnibioai-sif/bedtools")
    assert not pat.search("omnibioai-sif-extra")


@pytest.mark.parametrize("count,found", [(1, True), (0, False)])
def test_find_repo_row(count, found):
    page = MagicMock()
    rows = page.locator.return_value.filter.return_value.filter.return_value
    rows.count.return_value = count
    assert (mod.find_repo_row(page, "omnibioai-sif") is rows.last) is found


def test_find_visible_dialog_skips_hidden_matches():
    """Return the first visible match, skipping hidden dialogs ahead of it,
    and None when no match is visible."""
    page = MagicMock()
    matches = page.locator.return_value.filter.return_value
    hidden, shown = MagicMock(), MagicMock()
    hidden.is_visible.return_value, shown.is_visible.return_value = False, True
    matches.count.return_value = 2
    matches.nth.side_effect = lambda i: [hidden, shown][i]
    assert mod.find_visible_dialog(page, "Add repositor") is shown

    matches.nth.side_effect = lambda i: hidden
    assert mod.find_visible_dialog(page, "Add repositor") is None


def _dialog_page(monkeypatch, dialog=None):
    page = MagicMock()
    dialog = dialog or MagicMock()
    monkeypatch.setattr(mod, "find_visible_dialog", lambda p, hint: dialog)
    return page, dialog


def test_add_repository_success_and_failures(monkeypatch):
    """Return None when every step works, else the code for the failing step."""
    page, dialog = _dialog_page(monkeypatch)
    assert mod.add_repository(page, "omnibioai-sif") is None

    page.get_by_role.return_value.first.click.side_effect = TIMEOUT
    assert mod.add_repository(page, "r") == "no_add_repository_button"

    monkeypatch.setattr(mod, "find_visible_dialog", lambda p, hint: None)
    assert mod.add_repository(MagicMock(), "r") == "no_dialog"

    page, dialog = _dialog_page(monkeypatch)
    dialog.get_by_placeholder.return_value.first.fill.side_effect = TIMEOUT
    assert mod.add_repository(page, "r") == "no_search_box"

    page, dialog = _dialog_page(monkeypatch)
    monkeypatch.setattr(mod, "tick_repository", lambda p, d, r: None)
    assert mod.add_repository(page, "r") == "repo_not_selectable"

    confirm = MagicMock()
    confirm.click.side_effect = TIMEOUT
    monkeypatch.setattr(mod, "tick_repository", lambda p, d, r: confirm)
    assert mod.add_repository(page, "r") == "no_add_confirm_button"


def test_tick_repository_tries_each_row_style():
    """Stop at the first attempt that enables the confirm button, skipping
    attempts that raise or leave it disabled; None if none do."""
    dialog = MagicMock()
    confirm = dialog.get_by_role.return_value.last
    dialog.get_by_role.return_value.first.check.side_effect = TIMEOUT    # no checkbox role
    confirm.is_enabled.side_effect = [False, True]                        # label checkbox: no; option: yes
    assert mod.tick_repository(MagicMock(), dialog, "r") is confirm
    dialog.get_by_text.return_value.first.click.assert_not_called()

    confirm.is_enabled.side_effect = None
    confirm.is_enabled.return_value = False
    assert mod.tick_repository(MagicMock(), dialog, "r") is None
    dialog.get_by_text.return_value.first.click.assert_called()


def test_set_row_role_with_select():
    row = MagicMock()
    select = row.locator.return_value
    select.count.return_value = 1
    select.first.input_value.return_value = "write"
    assert mod.set_row_role(MagicMock(), row, "write") == "already_write"

    select.first.input_value.return_value = "read"
    assert mod.set_row_role(MagicMock(), row, "write") == "changed"
    select.first.select_option.assert_called_with(label="Write", timeout=5000)

    select.first.select_option.side_effect = TIMEOUT
    assert mod.set_row_role(MagicMock(), row, "write") == "role_select_failed"


def test_set_row_role_with_menu_button():
    row = MagicMock()
    row.locator.return_value.count.return_value = 0
    button = row.get_by_role.return_value.first
    button.inner_text.return_value = "Role: Write"
    assert mod.set_row_role(MagicMock(), row, "write") == "already_write"

    button.inner_text.return_value = "Role: Read"
    page = MagicMock()
    assert mod.set_row_role(page, row, "write") == "changed"
    page.locator.return_value.filter.assert_called_with(has_text="Write")

    button.click.side_effect = TIMEOUT
    assert mod.set_row_role(MagicMock(), row, "write") == "no_role_control"


@pytest.fixture
def grant(monkeypatch):
    """Patch the helpers grant_repo_access() composes; returns the dump log."""
    dumps = []
    monkeypatch.setattr(mod, "dump_debug_info", lambda page, name: dumps.append(name))
    return dumps


def _page(title="Package settings"):
    page = MagicMock()
    page.title.return_value = title
    return page


def test_grant_repo_access_paths(monkeypatch, grant):
    """Cover page-not-found, existing row, add-then-set, add failure, and a
    row that never appears or whose role cannot be set."""
    assert mod.grant_repo_access(_page("Page not found"), "o", "p", "r") == "page_not_found"

    row = object()
    monkeypatch.setattr(mod, "set_row_role", lambda page, r, role: "changed" if r is row else "bad")
    monkeypatch.setattr(mod, "find_repo_row", lambda page, repo: row)
    page = _page()
    assert mod.grant_repo_access(page, "omnibioai", "omnibioai-sif/bedtools", "r") == "changed"
    assert page.goto.call_args[0][0].endswith("/container/omnibioai-sif%2Fbedtools/settings")

    rows = iter([None, row])
    monkeypatch.setattr(mod, "find_repo_row", lambda page, repo: next(rows))
    monkeypatch.setattr(mod, "add_repository", lambda page, repo: None)
    assert mod.grant_repo_access(_page(), "o", "p", "r") == "changed"

    monkeypatch.setattr(mod, "find_repo_row", lambda page, repo: None)
    assert mod.grant_repo_access(_page(), "o", "p1", "r") == "repo_row_missing"
    monkeypatch.setattr(mod, "add_repository", lambda page, repo: "no_dialog")
    assert mod.grant_repo_access(_page(), "o", "p2", "r") == "no_dialog"

    monkeypatch.setattr(mod, "find_repo_row", lambda page, repo: object())
    assert mod.grant_repo_access(_page(), "o", "p3", "r") == "bad"
    assert grant == ["p1", "p2", "p3"]


class _PW:
    chromium = SimpleNamespace(launch=lambda **kw: SimpleNamespace(
        new_context=lambda **k: SimpleNamespace(new_page=lambda: MagicMock()),
        close=lambda: None))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Run main() with a saved session, isolated files, and no real browser."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "AUTH_STATE_FILE", str(tmp_path / "auth.json"))
    (tmp_path / "auth.json").write_text("{}")
    monkeypatch.setattr(mod, "LOG_FILE", str(tmp_path / "done.log"))
    monkeypatch.setattr(mod, "ORG_PACKAGES_FILE", str(tmp_path / "org_packages.txt"))
    monkeypatch.setattr(mod, "sync_playwright", lambda: _PW())
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["grant_package_access_browser.py", *argv])
        mod.main()
    return run


def test_main_login_and_missing_session(monkeypatch, cli, tmp_path):
    called = []
    monkeypatch.setattr(mod, "do_login", lambda: called.append(True))
    cli("--login")
    assert called == [True]

    Path(mod.AUTH_STATE_FILE).unlink()
    with pytest.raises(SystemExit):
        cli()


def test_main_only_and_listing_sources(monkeypatch, cli, tmp_path, capsys):
    """--only skips listing; otherwise use the API with GH_TOKEN, fall back to
    org_packages.txt, and exit when neither is available. Done packages are
    skipped."""
    results = {"omnibioai-sif/a": "already_write", "omnibioai-sif/b": "changed"}
    monkeypatch.setattr(mod, "grant_repo_access", lambda page, org, name, repo, role: results[name])

    cli("--only", "omnibioai-sif/a")
    assert "already_write: 1" in capsys.readouterr().out

    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(mod, "list_packages", lambda org, prefix, token: list(results))
    cli()
    out = capsys.readouterr().out
    assert "2 packages match" in out and "1 remaining" in out and "changed: 1" in out

    monkeypatch.delenv("GH_TOKEN")
    Path(mod.ORG_PACKAGES_FILE).write_text("omnibioai-sif/a\tprivate\nomnibioai-sif/b\tprivate\n")
    cli()
    assert "using" in capsys.readouterr().out

    Path(mod.ORG_PACKAGES_FILE).unlink()
    with pytest.raises(SystemExit):
        cli()


def test_main_failures_stop_or_continue(monkeypatch, cli, capsys):
    """Stop at the first failure by default; with --continue-on-fail count
    failures (including raised errors) and keep going."""
    def grant(page, org, name, repo, role):
        if name == "boom":
            raise RuntimeError("nope")
        return "no_dialog"

    monkeypatch.setattr(mod, "grant_repo_access", grant)
    cli("--only", "x", "--only", "y")
    out = capsys.readouterr().out
    assert "Stopping at first failure" in out and "[2/2]" not in out

    cli("--only", "x", "--only", "boom", "--continue-on-fail")
    out = capsys.readouterr().out
    assert "error: nope" in out and "failed: 2" in out

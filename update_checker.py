# UpdateChecker/update_checker.py
from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import FreeCAD

FREECAD_DOWNLOAD_URL = "https://www.freecad.org/downloads.php"


@dataclass
class UpdateResult:
    name: str
    current_version: str
    latest_version: str
    url: str = ""
    has_update: bool = False
    error: str = ""
    is_freecad: bool = False


# ---------------------------------------------------------------------------
# FreeCAD version check
# ---------------------------------------------------------------------------

def _get_freecad_version() -> str:
    try:
        vi = FreeCAD.Version()
        return ".".join(str(p) for p in vi[:3] if str(p).strip())
    except Exception:
        return "unknown"


def check_freecad_update() -> UpdateResult:
    current = _get_freecad_version()
    result = UpdateResult(
        name="FreeCAD",
        current_version=current,
        latest_version="",
        url=FREECAD_DOWNLOAD_URL,
        is_freecad=True,
    )
    try:
        data = _http_get_json("https://api.github.com/repos/FreeCAD/FreeCAD/releases/latest")
        tag = data.get("tag_name", "").lstrip("v")
        result.latest_version = tag
        result.has_update = _version_newer(tag, current)
    except Exception as exc:
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Addon scanning
# ---------------------------------------------------------------------------

def _addon_dirs() -> list:
    candidates = []
    try:
        user_data = Path(FreeCAD.getUserAppDataDir())
        candidates.append(user_data / "Mod")
    except Exception:
        pass
    return [d for d in candidates if d.is_dir()]


def _parse_package_xml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(path))
        root = tree.getroot()

        def _tag(el):
            tag = el.tag
            if "}" in tag:
                tag = tag.split("}")[1]
            return tag

        version = ""
        repo_url = ""
        repo_branch = "main"

        for child in root:
            if _tag(child) == "version" and child.text:
                version = child.text.strip()

        for url_el in root.iter():
            if _tag(url_el) == "url":
                url_type = url_el.get("type", "")
                if url_type in ("repository", "repo"):
                    repo_url = (url_el.text or "").strip()
                    repo_branch = url_el.get("branch", "main")
                    break

        return {"version": version, "repo_url": repo_url, "repo_branch": repo_branch}
    except Exception:
        return {}


def _fetch_remote_package_xml_version(repo_url: str, branch: str) -> tuple:
    owner_repo = _extract_owner_repo(repo_url)
    if not owner_repo:
        raise ValueError("Cannot parse GitHub owner/repo from: " + repo_url)

    raw_url = "https://raw.githubusercontent.com/" + owner_repo + "/" + branch + "/package.xml"
    release_url = "https://github.com/" + owner_repo + "/releases"

    try:
        content = _http_get_text(raw_url)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
        for child in root:
            tag = child.tag
            if "}" in tag:
                tag = tag.split("}")[1]
            if tag == "version" and child.text:
                return child.text.strip(), release_url
    except _NotFoundError:
        pass
    except Exception:
        pass

    return "", release_url


def check_addon_update(addon_path: Path) -> UpdateResult:
    name = addon_path.name
    local_info = _parse_package_xml(addon_path / "package.xml")
    local_version = local_info.get("version", "")
    repo_url = local_info.get("repo_url", "")
    repo_branch = local_info.get("repo_branch", "main")

    if not repo_url:
        repo_url = _detect_repo_url_from_git(addon_path)
    if not local_version:
        local_version = _read_git_head(addon_path) or "unknown"

    result = UpdateResult(name=name, current_version=local_version, latest_version="", url=repo_url or "")

    if not repo_url:
        result.error = "No upstream repo found"
        return result

    try:
        remote_version, release_url = _fetch_remote_package_xml_version(repo_url, repo_branch)
        result.url = release_url

        if remote_version:
            result.latest_version = remote_version
            if _looks_like_semver(local_version) and _looks_like_semver(remote_version):
                result.has_update = _version_newer(remote_version, local_version)
            else:
                result.has_update = remote_version != local_version
        else:
            remote_commit, commit_url = _fetch_latest_commit(repo_url)
            result.latest_version = remote_commit
            result.url = commit_url
            local_commit = _read_git_head(addon_path)
            result.has_update = bool(remote_commit) and remote_commit != local_commit

    except Exception as exc:
        result.error = str(exc)

    return result


def check_all_addons() -> list:
    results = []
    seen = set()
    for addon_dir in _addon_dirs():
        if not addon_dir.is_dir():
            continue
        for entry in sorted(addon_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith((".", "_")):
                continue
            if entry.name in seen:
                continue
            seen.add(entry.name)
            has_py = any(entry.glob("*.py"))
            has_pkg = (entry / "package.xml").exists()
            if not has_py and not has_pkg:
                continue
            results.append(check_addon_update(entry))
    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_checks() -> tuple:
    fc_result = [None]
    addon_results = [[]]

    def _check_fc():
        try:
            fc_result[0] = check_freecad_update()
        except Exception as exc:
            fc_result[0] = UpdateResult("FreeCAD", _get_freecad_version(), "", error=str(exc))

    def _check_addons():
        try:
            addon_results[0] = check_all_addons()
        except Exception as exc:
            FreeCAD.Console.PrintWarning("UpdateCheckWB addon scan error: " + str(exc) + "\n")

    t1 = threading.Thread(target=_check_fc, daemon=True)
    t2 = threading.Thread(target=_check_addons, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    return fc_result[0], addon_results[0]


# ---------------------------------------------------------------------------
# Qt dialog — must only be called from the main thread
# ---------------------------------------------------------------------------

def show_results_dialog(fc_result, addon_results):
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        import FreeCADGui
        from preferences import get, set as pref_set

        mw = FreeCADGui.getMainWindow()

        dialog = QtWidgets.QDialog(mw)
        dialog.setWindowTitle("Update Checker")
        dialog.setMinimumWidth(580)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        all_results = ([fc_result] if fc_result else []) + (addon_results or [])
        updates_available = [r for r in all_results if r and r.has_update]
        errors = [r for r in all_results if r and r.error and not r.has_update]
        up_to_date = [r for r in all_results if r and not r.has_update and not r.error]
        freecad_has_update = fc_result and fc_result.has_update
        addon_updates = [r for r in updates_available if not r.is_freecad]

        if updates_available:
            header = QtWidgets.QLabel("<b>Update Available!</b>")
        else:
            header = QtWidgets.QLabel("<b>Up to Date</b>")
        header.setStyleSheet("font-size: 14pt;")
        layout.addWidget(header)

        # ---- FreeCAD update message ----
        if freecad_has_update:
            lbl = QtWidgets.QLabel("<b style='color:#c0392b;'>&#x2B06; FreeCAD has an update</b>")
            layout.addWidget(lbl)
            layout.addWidget(_build_table([fc_result]))

        # ---- Addon updates ----
        if addon_updates:
            n = len(addon_updates)
            if n == 1:
                msg = "There's 1 addon update available"
            else:
                msg = "There are " + str(n) + " addon updates available"
            lbl = QtWidgets.QLabel("<b style='color:#c0392b;'>&#x2B06; " + msg + "</b>")
            layout.addWidget(lbl)
            layout.addWidget(_build_table(addon_updates))

        # ---- All up to date message ----
        if not updates_available:
            lbl = QtWidgets.QLabel("<b style='color:#27ae60;'>&#x2714; Everything is up to date</b>")
            layout.addWidget(lbl)

        # ---- Could not check ----
        if errors:
            group = QtWidgets.QGroupBox("Could not check (" + str(len(errors)) + ")")
            group.setCheckable(False)
            g_layout = QtWidgets.QVBoxLayout(group)
            for r in errors:
                g_layout.addWidget(QtWidgets.QLabel("<b>" + r.name + ":</b> " + r.error))
            layout.addWidget(group)

        # ---- Notification preferences ----
        pref_group = QtWidgets.QGroupBox("If there are updates, notify me...")
        pref_layout = QtWidgets.QVBoxLayout(pref_group)
        pref_layout.setSpacing(4)

        cooldown_options = [
            ("4 hours",   4),
            ("12 hours", 12),
            ("1 day",    24),
            ("1 week",  168),
            ("1 month", 720),
        ]

        current_mode = get("NotifyMode")
        current_cooldown = get("CooldownHours")

        rb_each = QtWidgets.QRadioButton("Each time a document is opened")
        rb_first = QtWidgets.QRadioButton("First document opened after starting FreeCAD")
        rb_never = QtWidgets.QRadioButton("Don't notify. Let me check manually")
        rb_never_row = QtWidgets.QHBoxLayout()
        rb_never_row.addWidget(rb_never)
        # Show the toolbar button icon as a hint of how to check manually
        import os
        _icon_path = ""
        _mod_dir = FreeCAD.getUserAppDataDir() + "Mod"
        if os.path.isdir(_mod_dir):
            for _n in os.listdir(_mod_dir):
                for _icon_name in ["FCUpdateCheck.svg", "update_checker_icon.svg"]:
                    _svg = os.path.join(_mod_dir, _n, "resources", _icon_name)
                    if os.path.exists(_svg):
                        _icon_path = _svg
                        break
                else:
                    continue
                break
        icon_lbl = QtWidgets.QLabel()
        if _icon_path:
            icon_lbl.setPixmap(QtGui.QIcon(_icon_path).pixmap(16, 16))
        else:
            icon_lbl.setPixmap(mw.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload).pixmap(16, 16))
        icon_lbl.setToolTip("Use the toolbar button or Tools menu to check manually")
        rb_never_row.addWidget(icon_lbl)
        rb_never_row.addStretch()

        cooldown_combo = QtWidgets.QComboBox()
        for label_text, hours in cooldown_options:
            cooldown_combo.addItem(label_text, hours)
        for i, (_, hours) in enumerate(cooldown_options):
            if hours == current_cooldown:
                cooldown_combo.setCurrentIndex(i)
                break

        rb_cooldown = QtWidgets.QRadioButton("Remind me after")
        rb_cooldown_row = QtWidgets.QHBoxLayout()
        rb_cooldown_row.addWidget(rb_cooldown)
        rb_cooldown_row.addWidget(cooldown_combo)
        rb_cooldown_row.addStretch()

        pref_layout.addWidget(rb_each)
        pref_layout.addWidget(rb_first)
        pref_layout.addLayout(rb_cooldown_row)
        pref_layout.addLayout(rb_never_row)

        if current_mode == "each_open":
            rb_each.setChecked(True)
        elif current_mode == "first_open":
            rb_first.setChecked(True)
        elif current_mode == "cooldown":
            rb_cooldown.setChecked(True)
        else:
            rb_never.setChecked(True)

        def _update_combo_state():
            cooldown_combo.setEnabled(rb_cooldown.isChecked())

        _update_combo_state()
        rb_each.toggled.connect(lambda _: _update_combo_state())
        rb_first.toggled.connect(lambda _: _update_combo_state())
        rb_cooldown.toggled.connect(lambda _: _update_combo_state())
        rb_never.toggled.connect(lambda _: _update_combo_state())

        layout.addWidget(pref_group)

        # ---- Action buttons ----
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()

        if freecad_has_update:
            dl_btn = QtWidgets.QPushButton("Download FreeCAD " + (fc_result.latest_version or "update"))
            dl_btn.setToolTip(FREECAD_DOWNLOAD_URL)
            dl_btn.clicked.connect(lambda: _open_url(FREECAD_DOWNLOAD_URL))
            btn_row.addWidget(dl_btn)

        # Always show Addon Manager button
        am_btn = QtWidgets.QPushButton("Open Addon Manager")
        am_btn.setToolTip("Open the Addon Manager")
        am_btn.clicked.connect(lambda: _open_addon_manager(dialog))
        btn_row.addWidget(am_btn)

        def _save_and_close():
            if rb_each.isChecked():
                pref_set("NotifyMode", "each_open")
            elif rb_first.isChecked():
                pref_set("NotifyMode", "first_open")
            elif rb_cooldown.isChecked():
                pref_set("NotifyMode", "cooldown")
                pref_set("CooldownHours", cooldown_combo.currentData())
            else:
                pref_set("NotifyMode", "never")
            dialog.accept()

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(_save_and_close)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)
        dialog.show()

    except Exception as exc:
        FreeCAD.Console.PrintWarning("UpdateCheckWB dialog error: " + str(exc) + "\n")


def _build_table(results):
    from PySide6 import QtWidgets, QtGui

    table = QtWidgets.QTableWidget(len(results), 3)
    table.setHorizontalHeaderLabels(["Component", "Installed", "Available"])
    table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)

    for row, r in enumerate(results):
        name_item = QtWidgets.QTableWidgetItem(r.name)
        if r.url:
            name_item.setToolTip(r.url)
            name_item.setForeground(QtGui.QColor("#2980b9"))
        table.setItem(row, 0, name_item)
        table.setItem(row, 1, QtWidgets.QTableWidgetItem(r.current_version or "-"))
        table.setItem(row, 2, QtWidgets.QTableWidgetItem(r.latest_version or "-"))

        if r.has_update:
            for col in range(3):
                item = table.item(row, col)
                if item:
                    item.setForeground(QtGui.QColor("#c0392b"))

    table.resizeRowsToContents()
    row_h = table.rowHeight(0) if len(results) > 0 else 24
    header_h = table.horizontalHeader().height()
    content_h = header_h + row_h * len(results) + 4
    # Cap at 8 rows before scrolling
    table.setFixedHeight(min(content_h, header_h + row_h * 8 + 4))

    def _on_cell_clicked(row, col):
        item = table.item(row, 0)
        if item and item.toolTip():
            _open_url(item.toolTip())

    table.cellClicked.connect(_on_cell_clicked)
    return table


def _open_addon_manager(parent):
    try:
        import FreeCADGui
        from PySide6 import QtWidgets, QtCore

        FreeCADGui.runCommand("Std_AddonMgr")

        def _set_filter():
            for widget in QtWidgets.QApplication.topLevelWidgets():
                if "Addon" in widget.windowTitle():
                    for combo in widget.findChildren(QtWidgets.QComboBox):
                        for i in range(combo.count()):
                            if "update" in combo.itemText(i).lower():
                                combo.setCurrentIndex(i)
                                break
                    break

        QtCore.QTimer.singleShot(2000, _set_filter)
    except Exception as exc:
        FreeCAD.Console.PrintWarning("UpdateCheckWB: could not open Addon Manager: " + str(exc) + "\n")
    parent.accept()


def _open_url(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

class _NotFoundError(Exception):
    pass


def _http_get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "FreeCAD-UpdateCheckWB/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _NotFoundError(url) from exc
        raise


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "FreeCAD-UpdateCheckWB/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _NotFoundError(url) from exc
        raise


def _detect_repo_url_from_git(addon_path: Path) -> str:
    git_config = addon_path / ".git" / "config"
    if git_config.exists():
        try:
            content = git_config.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("url ="):
                    return _ssh_to_https(line.split("=", 1)[1].strip())
        except Exception:
            pass
    repo_file = addon_path / "addon-repo.txt"
    if repo_file.exists():
        try:
            return repo_file.read_text(encoding="utf-8").strip().splitlines()[0]
        except Exception:
            pass
    return ""


def _read_git_head(addon_path: Path) -> str:
    head_file = addon_path / ".git" / "HEAD"
    if head_file.exists():
        try:
            ref = head_file.read_text().strip()
            if ref.startswith("ref: "):
                ref_path = addon_path / ".git" / ref[5:]
                if ref_path.exists():
                    return ref_path.read_text().strip()[:8]
            return ref[:8]
        except Exception:
            pass
    return ""


def _fetch_latest_commit(repo_url: str) -> tuple:
    owner_repo = _extract_owner_repo(repo_url)
    if not owner_repo:
        return "", repo_url
    try:
        data = _http_get_json("https://api.github.com/repos/" + owner_repo + "/commits")
        if data and isinstance(data, list):
            sha = data[0].get("sha", "")[:8]
            return sha, "https://github.com/" + owner_repo + "/commits"
    except Exception:
        pass
    return "", repo_url


def _extract_owner_repo(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if url.startswith(prefix):
            rest = url[len(prefix):]
            parts = rest.split("/")
            if len(parts) >= 2:
                return parts[0] + "/" + parts[1]
    return ""


def _ssh_to_https(url: str) -> str:
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    return url.rstrip("/").removesuffix(".git")


def _looks_like_semver(version: str) -> bool:
    if not version:
        return False
    return all(p.isdigit() for p in version.split(".") if p)


def _version_newer(candidate: str, current: str) -> bool:
    if _looks_like_semver(candidate) and _looks_like_semver(current):
        def _to_tuple(v):
            return tuple(int(x) for x in v.split(".") if x)
        try:
            return _to_tuple(candidate) > _to_tuple(current)
        except Exception:
            pass
    return candidate != current

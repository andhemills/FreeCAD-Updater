# UpdateChecker/InitGui.py
import FreeCAD
import FreeCADGui


class _UpdateCheckObserver:
    """
    Runs an update check in a background thread according to the
    user's chosen NotifyMode preference.

    A QTimer polls on the main thread every 500ms to check whether
    the background thread has finished, then shows the dialog safely
    on the main thread.
    """

    def __init__(self):
        self._last_check_time = 0
        self._check_in_progress = False
        self._first_open_done = False
        self._results = [None, []]
        self._results_pending = False
        self._poll_timer = None

    def slotActivateDocument(self, doc):
        self._maybe_check()

    def slotNewDocument(self, doc):
        self._maybe_check()

    def _maybe_check(self):
        import time
        from preferences import get

        mode = get("NotifyMode")

        if mode == "never":
            return
        if self._check_in_progress:
            return
        if mode == "first_open" and self._first_open_done:
            return
        if mode == "cooldown":
            cooldown_seconds = get("CooldownHours") * 3600
            if (time.time() - self._last_check_time) < cooldown_seconds:
                return

        self._first_open_done = True
        self._last_check_time = time.time()
        self._check_in_progress = True
        FreeCAD.Console.PrintLog("UpdateCheckWB: check triggered by document event.\n")
        self._start_poll_timer()
        self._run_check_in_background()

    def _start_poll_timer(self):
        from PySide6.QtCore import QTimer
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_for_results)
        self._poll_timer.start()

    def _poll_for_results(self):
        if not self._results_pending:
            return
        self._poll_timer.stop()
        self._poll_timer = None
        self._results_pending = False
        self._show_results()

    def _run_check_in_background(self):
        import threading
        import time

        def _run():
            t_start = time.time()
            FreeCAD.Console.PrintLog("UpdateCheckWB: starting network checks...\n")
            try:
                from update_checker import run_checks
                fc_result, addon_results = run_checks()
                self._results = [fc_result, addon_results]
            except Exception as exc:
                FreeCAD.Console.PrintWarning("UpdateCheckWB run_checks error: " + str(exc) + "\n")
                self._results = [None, []]
            finally:
                elapsed = round(time.time() - t_start, 1)
                FreeCAD.Console.PrintLog("UpdateCheckWB: checks finished in " + str(elapsed) + "s.\n")
                self._check_in_progress = False
                self._results_pending = True

        threading.Thread(target=_run, daemon=True).start()

    def _show_results(self):
        try:
            from update_checker import show_results_dialog
            show_results_dialog(self._results[0], self._results[1] or [])
        except Exception as exc:
            FreeCAD.Console.PrintWarning("UpdateCheckWB dialog error: " + str(exc) + "\n")


def _run_manual_check():
    """Run a manual check with a progress dialog, then always show results."""
    from PySide6 import QtWidgets, QtCore
    import threading

    mw = FreeCADGui.getMainWindow()
    progress = QtWidgets.QProgressDialog("Checking for updates...", "Cancel", 0, 0, mw)
    progress.setWindowTitle("Update Checker")
    progress.setMinimumDuration(0)
    progress.setValue(0)
    progress.show()

    results = [None, None]
    done = [False]

    def _worker():
        try:
            from update_checker import run_checks
            results[0], results[1] = run_checks()
        except Exception as exc:
            FreeCAD.Console.PrintWarning("UpdateCheckWB manual check error: " + str(exc) + "\n")
        finally:
            done[0] = True

    threading.Thread(target=_worker, daemon=True).start()

    poll_timer = QtCore.QTimer()

    def _poll():
        if progress.wasCanceled():
            poll_timer.stop()
            progress.close()
            return
        if not done[0]:
            return
        poll_timer.stop()
        progress.close()
        from update_checker import show_results_dialog
        show_results_dialog(results[0], results[1] or [])

    poll_timer.timeout.connect(_poll)
    poll_timer.start(200)


class UpdateCheckWorkbench(FreeCADGui.Workbench):
    MenuText = "Update Check"
    ToolTip = "Check for FreeCAD and workbench updates"
    Icon = ""

    def Initialize(self):
        pass

    def Activated(self):
        try:
            from update_checker import run_checks, show_results_dialog
            import threading
            from PySide6 import QtWidgets, QtCore
            import FreeCADGui as _FGui

            mw = _FGui.getMainWindow()
            progress = QtWidgets.QProgressDialog("Checking for updates...", "Cancel", 0, 0, mw)
            progress.setWindowTitle("Update Checker")
            progress.setMinimumDuration(0)
            progress.setValue(0)
            progress.show()

            results = [None, None]
            done = [False]

            def _worker():
                try:
                    results[0], results[1] = run_checks()
                except Exception:
                    pass
                finally:
                    done[0] = True

            threading.Thread(target=_worker, daemon=True).start()

            pt = QtCore.QTimer()

            def _poll():
                if progress.wasCanceled():
                    pt.stop()
                    progress.close()
                    return
                if not done[0]:
                    return
                pt.stop()
                progress.close()
                show_results_dialog(results[0], results[1] or [])

            pt.timeout.connect(_poll)
            pt.start(200)
        except Exception as exc:
            import FreeCAD
            FreeCAD.Console.PrintWarning("UpdateCheckWB Activated error: " + str(exc) + "\n")

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(UpdateCheckWorkbench())

# Register toolbar button and Tools menu item at startup
try:
    from PySide6 import QtWidgets, QtGui, QtCore
    import os

    mw = FreeCADGui.getMainWindow()

    # Find our own directory by searching all Mod subfolders for our InitGui.py
    _wb_dir = ""
    _mod_dir = FreeCAD.getUserAppDataDir() + "Mod"
    if os.path.isdir(_mod_dir):
        for _name in os.listdir(_mod_dir):
            _candidate = os.path.join(_mod_dir, _name)
            if os.path.exists(os.path.join(_candidate, "InitGui.py")) and \
               os.path.exists(os.path.join(_candidate, "update_checker.py")):
                _wb_dir = _candidate
                break
    icon_path = os.path.join(_wb_dir, "resources", "update_checker_icon.svg") if _wb_dir else ""
    if os.path.exists(icon_path):
        _wb_icon = QtGui.QIcon(icon_path)
    else:
        _wb_icon = mw.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)

    # --- Toolbar ---
    toolbar = QtWidgets.QToolBar("Update Check")
    toolbar.setObjectName("UpdateCheckerToolbar")
    btn = QtWidgets.QToolButton()
    btn.setIcon(_wb_icon)
    btn.setToolTip("Check for Updates")
    btn.clicked.connect(_run_manual_check)
    toolbar.addWidget(btn)
    mw.addToolBar(toolbar)

    # --- Tools menu — deferred so FreeCAD has finished building the menu bar ---
    def _add_tools_menu_item():
        try:
            import os
            import FreeCAD as _FC
            import FreeCADGui as _FGui
            from PySide6 import QtGui, QtWidgets

            _mw = _FGui.getMainWindow()

            # Load icon
            _icon = _mw.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)
            _mod_dir = _FC.getUserAppDataDir() + "Mod"
            if os.path.isdir(_mod_dir):
                for _n in os.listdir(_mod_dir):
                    _svg = os.path.join(_mod_dir, _n, "resources", "update_checker_icon.svg")
                    if os.path.exists(_svg):
                        _icon = QtGui.QIcon(_svg)
                        break

            def _check():
                from update_checker import run_checks, show_results_dialog
                import threading
                from PySide6 import QtWidgets as _QW, QtCore as _QC
                _mw2 = _FGui.getMainWindow()
                progress = _QW.QProgressDialog("Checking for updates...", "Cancel", 0, 0, _mw2)
                progress.setWindowTitle("Update Checker")
                progress.setMinimumDuration(0)
                progress.setValue(0)
                progress.show()
                results = [None, None]
                done = [False]
                def _worker():
                    try:
                        results[0], results[1] = run_checks()
                    except Exception:
                        pass
                    finally:
                        done[0] = True
                threading.Thread(target=_worker, daemon=True).start()
                pt = _QC.QTimer()
                def _poll():
                    if progress.wasCanceled():
                        pt.stop()
                        progress.close()
                        return
                    if not done[0]:
                        return
                    pt.stop()
                    progress.close()
                    show_results_dialog(results[0], results[1] or [])
                pt.timeout.connect(_poll)
                pt.start(200)

            menu_bar = _mw.menuBar()
            added = False
            for action in menu_bar.actions():
                if action.text().replace("&", "") == "Tools":
                    tools_menu = action.menu()
                    if tools_menu:
                        check_action = tools_menu.addAction(_icon, "Check for Updates")
                        check_action.setToolTip("Check FreeCAD and installed workbenches for updates")
                        check_action.triggered.connect(_check)
                        added = True
                    break
            if added:
                _FC.Console.PrintLog("UpdateCheckWB: added Tools menu item.\n")
            else:
                _FC.Console.PrintWarning("UpdateCheckWB: Tools menu not found.\n")
        except Exception as exc:
            FreeCAD.Console.PrintWarning("UpdateCheckWB: could not add Tools menu item: " + str(exc) + "\n")

    QtCore.QTimer.singleShot(5000, _add_tools_menu_item)

    FreeCAD.Console.PrintLog("UpdateCheckWB: toolbar and menu registered.\n")
except Exception as exc:
    FreeCAD.Console.PrintWarning("UpdateCheckWB: could not register toolbar: " + str(exc) + "\n")

# Register document observer
try:
    _observer = _UpdateCheckObserver()
    FreeCADGui.addDocumentObserver(_observer)
    FreeCAD.Console.PrintLog("UpdateCheckWB: document observer registered.\n")
except Exception as exc:
    FreeCAD.Console.PrintWarning("UpdateCheckWB: could not register observer: " + str(exc) + "\n")

# UpdateChecker/preferences.py
"""
Persistent preferences for the Update Checker workbench.
Stored in FreeCAD's user preferences under "UpdateChecker".

NotifyMode values:
    "each_open"     - check every time any document is opened
    "first_open"    - check only on the first document opened after FreeCAD starts
    "cooldown"      - check once per cooldown period, triggered by document open
    "never"         - never check automatically; manual only
"""

import FreeCAD

PREF_GROUP = "UpdateChecker"

DEFAULTS = {
    "NotifyMode":    "first_open",
    "CooldownHours": 24,   # Valid values: 4, 12, 24, 168, 720
}


def get(key):
    pref = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/" + PREF_GROUP)
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        return pref.GetBool(key, default)
    elif isinstance(default, int):
        return pref.GetInt(key, default)
    elif isinstance(default, str):
        return pref.GetString(key, default)
    return default


def set(key, value):
    pref = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/" + PREF_GROUP)
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        pref.SetBool(key, value)
    elif isinstance(default, int):
        pref.SetInt(key, value)
    elif isinstance(default, str):
        pref.SetString(key, value)

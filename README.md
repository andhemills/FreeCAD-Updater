# UpdateCheckWB — FreeCAD Update Check Workbench

Automatically checks for updates to FreeCAD itself and all installed addons/workbenches when FreeCAD starts. A results dialog appears only when updates are available, so it stays out of your way when everything is current.

## Features

- Checks the running FreeCAD version against the latest GitHub release
- Scans every addon in your Mod directory for a newer version
- Detects addon repos via `package.xml`, `.git/config`, or a plain `addon-repo.txt` file
- Runs entirely in a background thread — FreeCAD loads at full speed
- Results dialog appears ~5 seconds after startup (only if updates exist)
- "Check for Updates" command available manually under **Tools → Check for Updates**
- Links to the release page or repo for each out-of-date component
- One-click "Open Addon Manager" button to install available updates

## Installation

### Via Addon Manager (recommended)

1. Open **Tools → Addon Manager**
2. Search for `UpdateCheckWB`
3. Click Install

### Manual installation

1. Download or clone this repository:
   ```
   git clone https://github.com/YOURUSER/UpdateCheckWB.git
   ```
2. Copy the `UpdateCheckWB` folder into your FreeCAD `Mod` directory:
   - **Linux:** `~/.local/share/FreeCAD/Mod/`
   - **macOS:** `~/Library/Preferences/FreeCAD/Mod/`
   - **Windows:** `%APPDATA%\FreeCAD\Mod\`
3. Restart FreeCAD.

## How addon detection works

For each subdirectory in your Mod folder the workbench looks for an upstream repo in this order:

| File | What it reads |
|---|---|
| `package.xml` | `<url type="repository">` element |
| `.git/config` | `[remote "origin"]` → `url` |
| `addon-repo.txt` | First line of the file (plain URL) |

If a repo URL is found and points to GitHub, the workbench first tries `/releases/latest`. If no releases exist it falls back to comparing the latest commit hash on the default branch.

### Adding `addon-repo.txt` to your own workbench

If your workbench doesn't use the Addon Manager metadata format or isn't a git clone, create a file called `addon-repo.txt` in your workbench root containing just the GitHub URL:

```
https://github.com/yourname/YourWorkbench
```

## Configuration

Open `update_checker.py` and edit the constants near the top:

| Setting | Default | Description |
|---|---|---|
| Startup delay | 5000 ms | Time after launch before the check runs |
| Check timeout | 10 s | Per-request network timeout |
| GitHub token | (none) | Add `Authorization` header to raise the API rate limit from 60 to 5000 requests/hour |

## Requirements

- FreeCAD 0.20 or newer
- Python 3.8+ (bundled with FreeCAD)
- Internet access at startup (checks are skipped gracefully if offline)

## Troubleshooting

**No dialog appears even though I know updates exist**

Check the FreeCAD Report View (`View → Panels → Report View`) for lines starting with `UpdateCheckWB:`. If you see a network error, check your firewall or proxy settings.

**GitHub API rate limit exceeded**

The unauthenticated GitHub API allows 60 requests per hour per IP. If you have many addons, add a [personal access token](https://github.com/settings/tokens) to `update_checker.py`:

```python
"Authorization": "Bearer ghp_YOURTOKEN",
```

**An addon shows "No upstream repo found"**

The addon has no `package.xml`, no `.git` directory, and no `addon-repo.txt`. Create an `addon-repo.txt` file in the addon's root directory (see above).

## License

LGPL-2.1 — same as FreeCAD itself.

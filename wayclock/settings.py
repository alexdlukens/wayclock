"""wayclock settings state + JSON persistence to a snap-writable path.

On a snap install, settings are written to $SNAP_USER_COMMON/settings.json —
a directory snapd auto-creates and keeps writable under strict confinement,
shared across snap revisions (so a `snap refresh` keeps user settings). In
development (no SNAP_USER_COMMON) we fall back to $XDG_CONFIG_HOME/wayclock/
settings.json (default ~/.config/wayclock/settings.json).

Writes are atomic (temp file + os.replace) so a crash never corrupts the file.
"""

import json
import os
from dataclasses import asdict, dataclass

# ---- defaults & palette (single place to restyle) ----
OPACITY_MIN = 0.15
OPACITY_MAX = 1.0
THEMES = ("light", "dark", "tan")

# accent color keys -> (r, g, b); used for the second hand + swatches.
ACCENTS = {
    "red": (0.85, 0.27, 0.22),
    "blue": (0.20, 0.45, 0.90),
    "green": (0.20, 0.70, 0.35),
    "amber": (0.95, 0.60, 0.15),
    "violet": (0.55, 0.30, 0.85),
}


@dataclass
class Settings:
    opacity: float = OPACITY_MAX
    theme: str = "light"
    accent: str = "red"

    def to_dict(self):
        return asdict(self)


def _config_dir():
    """Snap-writable dir, or the per-user config dir in dev."""
    common = os.environ.get("SNAP_USER_COMMON")
    if common:
        return common
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, "wayclock")
    return os.path.join(os.path.expanduser("~"), ".config", "wayclock")


def config_path():
    return os.path.join(_config_dir(), "settings.json")


def load():
    """Read settings from disk; any missing/invalid field keeps its default."""
    s = Settings()
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return s
    if not isinstance(data, dict):
        return s
    if isinstance(data.get("opacity"), (int, float)):
        s.opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(data["opacity"])))
    if data.get("theme") in THEMES:
        s.theme = data["theme"]
    if data.get("accent") in ACCENTS:
        s.accent = data["accent"]
    return s


def save(s):
    """Write settings atomically; return True on success."""
    d = _config_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return False
    path = config_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s.to_dict(), f, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False

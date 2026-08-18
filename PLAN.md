# wayclock — floating analog clock (Python + GTK, snap-packaged)

A small GTK application written in Python: a circular, high-quality analog
clock composited with per-pixel transparency so it appears to float over the
desktop. Packaged as a strict-confinement snap built with uv.

## 1. Acceptance criteria

- Circular clock face; every pixel outside the circle has alpha 0 — a true
  circle on any wallpaper/window, not a rounded rectangle.
- "Floating": no decorations, no taskbar entry, reserves no layout space,
  stays above other windows, clicks pass through. (Constraint for this box:
  see §2.1.)
- Accurate local time: hour/minute/second hands, 12 hour ticks, 60 minute
  ticks, antialiased, smooth-sweep second hand at vsync.
- Idle CPU ≈ 0; redraw only when the clock moves.
- Correct at HiDPI.
- Builds and runs as a strict-confinement snap on this machine.

## 2. Environment facts (measured 2026-08-18)

| Fact | Value | Consequence |
|---|---|---|
| Compositor | GNOME Shell 46 / Mutter, native Wayland (`wayland-0`) | **No `zwlr_layer_shell_v1`** (verified by registry dump) — floating not possible client-side on this box |
| Python | 3.12.3, PyGObject 3.48.2, pycairo 1.25.1, gi cairo binding OK | Host has everything needed for dev |
| GTK | 3.24 **and** 4.14 installed | Both available; we choose GTK3 (see §9) |
| uv | 0.10.0 (`~/.local/bin/uv`) | Project/venv manager available, no install needed |
| Packaging | snapd + snapcraft present | Snap build + `snap install --dangerous` verifiable here |
| `gtk-layer-shell` | Python module absent; C lib apt-installable | Optional enhancement, see §4.2 |
| `grim` | not installed | Install via apt for the transparency proof (M2) |

### 2.1 The one hard constraint

"Floating" on Wayland = `zwlr_layer_shell_v1` (waybar, wlogout use it). Mutter
46 does not serve it, and no other client-side protocol on Mutter produces an
undecorated always-on-top surface. Decision:

- **Primary path**: `gtk-layer-shell` (Python) when available — floats on
  wlroots compositors (sway, labwc, river), Hyprland, KDE.
- **Fallback path**: normal GTK window (auto-selected). Still circular + fully
  transparent; only loses "floating" — this is what runs on this GNOME box.
- **Snap reality**: layer-shell is optional and off by default; the core app
  and snap must not depend on it.

## 3. Architecture

GTK replaces almost all the Wayland plumbing the C version needed. GLib's main
loop dispatches events; GDK owns the surface, buffers, damage, and HiDPI scale;
the **frame clock** (`Gtk.Window.add_tick_callback`) gives vsync pacing. We
write only: window setup (RGBA visual), one cairo draw function, one tick
callback.

```
GLib main loop (GTK3)
   │
   └─ GdkFrameClock (vsync) ── tick callback
        │  time changed? → window.queue_draw()
        ▼
   draw callback (DrawingArea::draw)
        │  cairo ctx (pycairo)
        ▼
   circle, ticks, hands → compositor composites alpha
```

Buffers/surface: `wl_shm` handled by GDK (GTK3 Wayland backend). Alpha:
per-pixel from the RGBA visual; nothing drawn outside the circle → transparent
→ the desktop shows through. HiDPI: `window.get_scale_factor()` multiplies the
drawing buffer; cairo AA stays crisp.

```mermaid
flowchart LR
    A[uv run wayclock] --> B[app.py]
    B --> C{RGBA visual?}
    C -->|yes| D[window transparent]
    C -->|no| E[degrade: warn, opaque bg]
    D --> F{gtk_layer_shell<br/>importable?}
    F -->|yes| G[overlay layer, top-right,<br/>exclusive_zone -1, click-through]
    F -->|no| H[normal window fallback]
    G --> I[tick callback → queue_draw]
    H --> I
    I --> J[clock.py: cairo draw]
```

## 4. Component design

### 4.1 Window & transparency (`app.py`)

- `Gtk.Window`, square, fixed logical size (e.g. 320 px), `resizable=False`.
- RGBA visual recipe (GTK3, battle-tested):
  ```python
  screen = window.get_screen()
  visual = screen.get_rgba_visual()
  if visual is None:        # compositor without ARGB — practically never on Wayland
      warn and use default
  window.set_visual(visual)
  window.set_app_paintable(True)
  ```
- Draw callback paints the window background with alpha 0 first — only the
  circle is visible; the window rectangle itself is invisible.
- No `set_decorated(False)` outside layer-shell mode (Mutter ignores it and
  it breaks the fallback on X11); layer-shell mode is undecorated by nature.
- Lifecycle: default GTK signal handling for SIGINT/SIGTERM; `Gtk.main()` loop.

### 4.2 Layer-shell — optional, runtime-detected

- `try: import gtk_layer_shell` (`pip install gtk-layer-shell` +
  `apt install libgtk-layer-shell0`); on `ImportError` → fallback window.
- When present:
  ```python
  gtk_layer_shell.init_for_window(win)
  gtk_layer_shell.set_layer(win, gtk_layer_shell.Layer.TOP)
  gtk_layer_shell.set_anchor(win, gtk_layer_shell.Edge.TOP | gtk_layer_shell.Edge.RIGHT)
  gtk_layer_shell.set_margin(win, 24)
  gtk_layer_shell.set_exclusive_zone(win, -1)      # floats, reserves nothing
  gtk_layer_shell.set_keyboard_mode(win, gtk_layer_shell.KeyboardMode.NONE)
  ```
- No click-through needed: exclusive_zone −1 + no input handlers; pointer
  events simply do nothing.
- Never required at build/run time.

### 4.3 Rendering (`clock.py`)

Pure function `draw_clock(ctx, size, now)` — cairo only, no GTK state,
testable against hand angles. Drawing order (same geometry as v1 plan):

1. **Soft shadow** — radial-gradient disk, low alpha, +3 px offset.
2. **Face** — radial gradient (near-white core → slightly darker edge),
   `alpha ≈ 0.92`, translucent.
3. **Rim** — 2 px ring: darker outer + lighter inner highlight.
4. **Ticks** — 60 minute ticks (short, ~30% alpha), 12 hour ticks (long,
   bold, ~90% alpha) drawn on top; `sin/cos` placement inside the rim.
5. **Hands** — `CAIRO_LINE_CAP_ROUND` strokes: hour 0.45R × 7 px,
   minute 0.60R × 5 px, second 0.72R × 2 px with 0.15R counterweight tail;
   faint offset shadow stroke under each. Center cap: base disc + accent disc.
6. Angles from local time (`datetime.now()`):
   - hour   = `(h % 12 + m/60 + s/3600) × 30°`
   - minute = `(m + s/60) × 6°`
   - second = `(s + µs/1e6) × 6°` → sub-degree resolution for the sweep.

Sizes/colors are module constants at the top of `clock.py` (single theme
point).

### 4.4 Pacing & CPU

- `window.add_tick_callback(...)` — GTK3 frame clock, fires per vsync.
- Callback reads the time; if the second-hand angle moved ≥ ~1 frame's worth
  since the last draw → `queue_draw()`; always return `True` (keep ticking).
- Result: smooth sweep at vsync, ~0 idle CPU (no redraw when nothing moved,
  no polling — the frame clock only fires on vsync).

### 4.5 Dev workflow (uv)

```sh
uv venv --system-site-packages .venv   # gi + pycairo resolve from host apt packages
uv pip install -e .                    # installs the wayclock package (no 3rd-party deps)
uv run wayclock                        # run against host Wayland session
```

- Decision: **`--system-site-packages` is required** — PyGObject (`gi`) and
  pycairo are system packages (apt `python3-gi`, `python3-gi-cairo`); they are
  not pip-installable without building GObject introspection against system
  libs, which is wrong inside a snap. uv's role: reproducible venv + runner +
  lockfile discipline. `pyproject.toml` declares `requires-python >= 3.12` and
  **zero** third-party dependencies (the clock needs none) — `uv.lock` stays
  trivial, but the venv is still the packaging unit for the snap.

## 5. Project layout

```
wayclock/
  pyproject.toml            # uv project; [project.scripts] wayclock = wayclock.app:main
  uv.lock                   # generated
  src/wayclock/
    __init__.py
    app.py                  # window, RGBA visual, tick callback, layer-shell hook, main()
    clock.py                # draw_clock(ctx, size, now) — pure cairo
  snap/
    snapcraft.yaml
    local/launch            # snap entry: exec "$SNAP/venv/bin/python3" -m wayclock
```

## 6. Snap packaging

`snap/snapcraft.yaml` — base `core24`, confinement `strict`, one app:

```yaml
apps:
  wayclock:
    command: local/launch
    plugs: [wayland, desktop]
    environment:
      GDK_BACKEND: wayland
```

Parts:

1. **`python`** — `stage-packages` from the 24.04 archive (matches host
   versions): `python3`, `python3-gi`, `python3-gi-cairo`, `python3-cairo`
   (pycairo: `import cairo`), `gir1.2-gtk-3.0`. `libgtk-3-0`,
   `libgirepository-1.0-1`, and `libcairo2` arrive as their dependencies —
   no need to list them. No GNOME extension needed: we draw everything
   ourselves (no themes/fonts/icons to stage).
2. **`wayclock`** — copies `wayclock/` into `$CRAFT_PRIME/wayclock` and
   `snap/local/launch`; `override-prime` creates the venv **from the base
   python with `--system-site-packages`**, so gi and pycairo resolve from
   the staged packages, and `--without-pip` keeps pip/uv out of the snap:
   ```sh
   /usr/bin/python3 -m venv --copies --system-site-packages \
       --without-pip $CRAFT_PRIME/venv
   # rewrite pyvenv.cfg: home = /usr/bin  (so it resolves inside $SNAP)
   sed -i 's|^home = .*|home = /usr/bin|' $CRAFT_PRIME/venv/pyvenv.cfg
   ```
   Rewriting `pyvenv.cfg` home is what makes the venv python resolve the
   staged stdlib inside `$SNAP` — the one genuinely tricky part of shipping
   a venv in a snap; milestone M5 proves it.

Build & install:

```sh
cd wayclock && snapcraft            # needs the snapcraft runner (present)
sudo snap install --dangerous wayclock_*.snap
snap run wayclock
```

Optional layer-shell part (only when floating on non-Mutter compositors is
wanted): stage `libgtk-layer-shell0`, `pip install gtk-layer-shell` into the
same venv.

## 7. Milestones & verification

| # | Milestone | Verify |
|---|---|---|
| M1 | Window + circle renders | `uv run wayclock`: circle visible top-right; no GTK warnings; fallback window has titlebar (expected here) |
| M2 | Circular transparency | `sudo apt install grim`; `grim -g <clock-geometry>` → PNG: pixels outside circle alpha 0; drag a window behind → shows through around the circle |
| M3 | Correct time | Compare hand angles vs `date +%T` every second for 10 s, within 1° |
| M4 | Sweep + low CPU | Second hand moves continuously; `pidstat -p <pid> 1` shows ≈ 0% between frames |
| M5 | Snap works | `snapcraft` succeeds; `snap install --dangerous`; `snap run wayclock` renders the clock in the confined snap on this Wayland session; venv resolves (proof: gi imports from `$SNAP/usr`) |
| M6 | Layer-shell (optional) | On a wlroots/Hyprland/KDE box: overlay top-right, no decorations, exclusive_zone −1, click-through. Not verifiable on this machine — marked optional |

M1–M5 are required; M6 is an environment-dependent bonus.

## 8. Risks & decisions

- **GTK3 over GTK4**: RGBA-visual transparency is battle-tested and
  `gtk-layer-shell`'s Python binding targets GTK3. GTK4's transparency +
  layer-shell story is younger and more compositor-sensitive. Both runtimes
  are installed; the plan pins GTK3 and notes GTK4 as a future port.
- **Floating on this box is impossible for any client app** (measured: no
  layer-shell on Mutter 46). Plan ships fallback + optional layer-shell;
  if floating on *this* GNOME box is the real goal, that's a GNOME Shell
  extension — different stack, out of scope.
- **venv-in-snap relocation**: the prime-time creation + `pyvenv.cfg` `home`
  rewrite is the known-good pattern; M5 is the gate. If it misbehaves,
  fallback is staging `python3-gi` and running the staged system python
  directly (no venv) — but we ship the venv first, per requirement.
- **Zero third-party deps**: the clock needs nothing beyond gi/pycairo, so
  `uv.lock` is nearly empty. The venv still matters: it is the snap's Python
  runtime unit and the dev reproducibility mechanism.
- **GTK3 in a strict snap**: needs `wayland` plug only for rendering; `desktop`
  plug added for sane GTK settings/theme lookup. No X11 path required
  (GDK_BACKEND forced).

## 9. Out of scope

Config files/CLI flags, drag-to-move, themes, multiple clocks, click actions,
GNOME Shell extension variant. All have a stated extension point in
`clock.py` constants or `app.py` setup.

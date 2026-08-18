"""Cairo rendering for the analog clock face and the settings flip card.

`draw_clock(ctx, size, now, style)` draws the complete clock (shadow, face,
rim, ticks, settings icon, hands, center cap) into a cairo context whose
coordinate space is a `size x size` square in logical pixels, centered.
`draw_settings` draws the back face (the settings panel). No GTK state is
touched, so the renderer is unit-testable against hand angles.

The settings entry icon is the Lucide `settings` icon (wayclock/assets/
settings.svg), rasterized with librsvg and stroke-tinted to the style's rim
colour; librsvg ships with the snap (gnome extension) and on the dev host
(gir1.2-rsvg-2.0).

Colours come from a `Style` (built by `style_from(settings)`); `settings_layout`
and the `*_hit` helpers expose the pointer hit-test regions for app.py.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cairo
try:
    import gi
    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg
except (ImportError, ValueError) as exc:
    raise ImportError(
        "wayclock renders the settings icon with librsvg; install "
        "gir1.2-rsvg-2.0 (dev) — the snap bundles it via the gnome extension"
    ) from exc

from .settings import ACCENTS, OPACITY_MAX, OPACITY_MIN, THEMES

# ---- fixed geometry ----
SHADOW_OFFSET = 3.0
RIM_WIDTH = 2.5
HOUR_TICK_W = 4.0
MINUTE_TICK_W = 1.5
HAND_W = {"hour": 7.0, "minute": 5.0, "second": 2.0}
HAND_LEN = {"hour": 0.45, "minute": 0.60, "second": 0.72}
SECOND_TAIL = 0.15
CAP_R = 5.0
CAP_ACCENT_R = 2.5

# settings icon (front face) — Lucide `settings`, local asset
GEAR_FRAC = 0.16      # icon radius as a fraction of R
GEAR_POS = 0.58       # icon centre offset below the clock centre (fraction of R);
                      #   keeps the icon above the 6 o'clock tick: icon bottom
                      #   edge = GEAR_POS + GEAR_FRAC = 0.74R vs tick inner end
                      #   0.78R minus its 2 px round cap ≈ 0.77R → ~5 px gap
_ICON_PATH = Path(__file__).resolve().parent / "assets" / "settings.svg"
_ICON_VIEWBOX = 24.0  # the lucide icon is 24x24
_icon_handle = Rsvg.Handle.new_from_data(_ICON_PATH.read_bytes())


@dataclass(frozen=True)
class Style:
    """Colour set for one rendering pass (theme + accent + opacity baked in)."""
    face_core: tuple
    face_edge: tuple
    rim: tuple
    rim_highlight: tuple
    tick_hour: tuple
    tick_minute: tuple
    hand_hour: tuple
    hand_minute: tuple
    hand_second: tuple
    shadow: tuple


# ---- theme presets (RGB + base alpha; opacity multiplies alpha) ----
LIGHT = Style(
    face_core=(1.00, 1.00, 1.00, 0.95),
    face_edge=(0.90, 0.92, 0.96, 0.88),
    rim=(0.16, 0.18, 0.24, 0.90),
    rim_highlight=(1.00, 1.00, 1.00, 0.55),
    tick_hour=(0.12, 0.14, 0.20, 0.95),
    tick_minute=(0.12, 0.14, 0.20, 0.35),
    hand_hour=(0.12, 0.14, 0.20, 0.95),
    hand_minute=(0.14, 0.16, 0.22, 0.95),
    hand_second=(0.85, 0.27, 0.22, 0.95),
    shadow=(0.00, 0.00, 0.00, 0.16),
)

DARK = Style(
    face_core=(0.16, 0.17, 0.22, 0.95),
    face_edge=(0.08, 0.09, 0.13, 0.90),
    rim=(0.90, 0.92, 0.96, 0.75),
    rim_highlight=(1.00, 1.00, 1.00, 0.25),
    tick_hour=(0.90, 0.92, 0.96, 0.95),
    tick_minute=(0.90, 0.92, 0.96, 0.35),
    hand_hour=(0.90, 0.92, 0.96, 0.95),
    hand_minute=(0.80, 0.84, 0.90, 0.95),
    hand_second=(0.85, 0.27, 0.22, 0.95),
    shadow=(0.00, 0.00, 0.00, 0.30),
)

_PRESETS = {"light": LIGHT, "dark": DARK}


def _ma(color, opacity):
    """Scale a colour's alpha by `opacity`."""
    return (color[0], color[1], color[2], color[3] * opacity)


def style_from(settings):
    """Effective style for the current settings (theme, accent, opacity)."""
    base = _PRESETS[settings.theme]
    o = settings.opacity
    accent = ACCENTS[settings.accent]
    return Style(
        face_core=_ma(base.face_core, o),
        face_edge=_ma(base.face_edge, o),
        rim=_ma(base.rim, o),
        rim_highlight=_ma(base.rim_highlight, o),
        tick_hour=_ma(base.tick_hour, o),
        tick_minute=_ma(base.tick_minute, o),
        hand_hour=_ma(base.hand_hour, o),
        hand_minute=_ma(base.hand_minute, o),
        hand_second=(accent[0], accent[1], accent[2], base.hand_second[3] * o),
        shadow=base.shadow,
    )


def _polar(cx, cy, r, deg):
    """Point at radius `r` on the clock, `deg` clockwise from 12 o'clock."""
    a = math.radians(deg - 90.0)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def hand_angles(now):
    """(hour, minute, second) hand angles in degrees, clockwise from 12."""
    s = now.second + now.microsecond / 1.0e6
    m = now.minute + s / 60.0
    h = now.hour % 12 + m / 60.0
    return h * 30.0, m * 6.0, s * 6.0


def _shadow_disk(ctx, cx, cy, R, style):
    r = R + 4.0
    g = cairo.RadialGradient(
        cx + SHADOW_OFFSET, cy + SHADOW_OFFSET, R * 0.5,
        cx + SHADOW_OFFSET, cy + SHADOW_OFFSET, r,
    )
    g.add_color_stop_rgba(0.0, *style.shadow)
    g.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
    ctx.set_source(g)
    ctx.arc(cx + SHADOW_OFFSET, cy + SHADOW_OFFSET, r, 0.0, 2.0 * math.pi)
    ctx.fill()


def _face(ctx, cx, cy, R, style):
    g = cairo.RadialGradient(cx - R * 0.35, cy - R * 0.35, R * 0.1, cx, cy, R)
    g.add_color_stop_rgba(0.0, *style.face_core)
    g.add_color_stop_rgba(1.0, *style.face_edge)
    ctx.set_source(g)
    ctx.arc(cx, cy, R, 0.0, 2.0 * math.pi)
    ctx.fill()


def _rim(ctx, cx, cy, R, style):
    ctx.set_line_width(RIM_WIDTH)
    ctx.set_source_rgba(*style.rim)
    ctx.arc(cx, cy, R, 0.0, 2.0 * math.pi)
    ctx.stroke()
    ctx.set_line_width(1.0)
    ctx.set_source_rgba(*style.rim_highlight)
    ctx.arc(cx, cy, R - RIM_WIDTH, 0.0, 2.0 * math.pi)
    ctx.stroke()


def _ticks(ctx, cx, cy, R, style):
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    for i in range(60):
        deg = i * 6.0
        if i % 5 == 0:
            x1, y1 = _polar(cx, cy, R * 0.78, deg)
            x2, y2 = _polar(cx, cy, R * 0.92, deg)
            ctx.set_line_width(HOUR_TICK_W)
            ctx.set_source_rgba(*style.tick_hour)
        else:
            x1, y1 = _polar(cx, cy, R * 0.85, deg)
            x2, y2 = _polar(cx, cy, R * 0.92, deg)
            ctx.set_line_width(MINUTE_TICK_W)
            ctx.set_source_rgba(*style.tick_minute)
        ctx.move_to(x1, y1)
        ctx.line_to(x2, y2)
        ctx.stroke()


def gear_center(size):
    """Centre of the gear icon on the front face (bottom centre)."""
    cx = cy = size / 2.0
    R = size / 2.0 - 6.0
    return cx, cy + R * GEAR_POS


def gear_hit(size, x, y):
    gx, gy = gear_center(size)
    gr = (size / 2.0 - 6.0) * GEAR_FRAC
    return (x - gx) ** 2 + (y - gy) ** 2 <= (gr * 1.25) ** 2


def _settings_icon(ctx, gx, gy, gr, style):
    """Lucide `settings` icon, stroke-tinted to the rim colour, radius `gr`.

    The SVG keeps `stroke="currentColor"`; a stylesheet sets `color` per
    render, so one local asset serves every theme/opacity without re-encoding.
    The handle is reused — `set_stylesheet` is cheap and rendering is
    synchronous, so the shared handle never sees a torn state.
    """
    r, g, b, a = style.rim
    css = "* { color: rgba(%d, %d, %d, %.3f); }" % (
        round(r * 255), round(g * 255), round(b * 255), a)
    _icon_handle.set_stylesheet(css.encode())
    ctx.save()
    ctx.translate(gx, gy)
    s = 2.0 * gr / _ICON_VIEWBOX
    ctx.scale(s, s)
    ctx.translate(-_ICON_VIEWBOX / 2.0, -_ICON_VIEWBOX / 2.0)
    viewport = Rsvg.Rectangle()
    viewport.x = viewport.y = 0.0
    viewport.width = viewport.height = _ICON_VIEWBOX
    _icon_handle.render_document(ctx, viewport)
    ctx.restore()


def _hand(ctx, cx, cy, R, deg, length, width, color, style, tail=0.0):
    x0, y0 = _polar(cx, cy, -R * tail, deg)   # tail extends behind the center
    x1, y1 = _polar(cx, cy, R * length, deg)
    ctx.save()
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_width(width)
    ctx.set_source_rgba(*style.shadow)
    ctx.move_to(x0 + SHADOW_OFFSET, y0 + SHADOW_OFFSET)
    ctx.line_to(x1 + SHADOW_OFFSET, y1 + SHADOW_OFFSET)
    ctx.stroke()
    ctx.set_source_rgba(*color)
    ctx.move_to(x0, y0)
    ctx.line_to(x1, y1)
    ctx.stroke()
    ctx.restore()


def _cap(ctx, cx, cy, style):
    ctx.set_source_rgba(*style.hand_minute)
    ctx.arc(cx, cy, CAP_R, 0.0, 2.0 * math.pi)
    ctx.fill()
    ctx.set_source_rgba(*style.hand_second)
    ctx.arc(cx, cy, CAP_ACCENT_R, 0.0, 2.0 * math.pi)
    ctx.fill()


def draw_face(ctx, size, style):
    """Static part: shadow, face, rim, ticks, settings icon. Cacheable offscreen."""
    cx = cy = size / 2.0
    R = size / 2.0 - 6.0
    _shadow_disk(ctx, cx, cy, R, style)
    _face(ctx, cx, cy, R, style)
    _rim(ctx, cx, cy, R, style)
    _ticks(ctx, cx, cy, R, style)
    gx, gy = gear_center(size)
    _settings_icon(ctx, gx, gy, R * GEAR_FRAC, style)


def draw_hands(ctx, size, now, style):
    """Time-dependent part: the three hands and the center cap."""
    cx = cy = size / 2.0
    R = size / 2.0 - 6.0
    h_a, m_a, s_a = hand_angles(now)
    _hand(ctx, cx, cy, R, h_a, HAND_LEN["hour"], HAND_W["hour"],
          style.hand_hour, style)
    _hand(ctx, cx, cy, R, m_a, HAND_LEN["minute"], HAND_W["minute"],
          style.hand_minute, style)
    _hand(ctx, cx, cy, R, s_a, HAND_LEN["second"], HAND_W["second"],
          style.hand_second, style, tail=SECOND_TAIL)
    _cap(ctx, cx, cy, style)


def draw_clock(ctx, size, now, style):
    """Render the full clock front. `now` is a datetime with µs precision."""
    draw_face(ctx, size, style)
    draw_hands(ctx, size, now, style)


# ============================== settings panel ==============================

def settings_layout(size):
    """Hit-test regions for the back (settings) face.

    Returns a dict with a track rect and swatch circles; used by both the
    renderer and the hit-tests in `settings_hit` / `opacity_from_x`.
    """
    cx = cy = size / 2.0
    R = size / 2.0 - 6.0
    return {
        "opacity": (cx - R * 0.30, cy - R * 0.48, R * 0.95, R * 0.10),
        "theme": [(cx - R * 0.16, cy - R * 0.18, R * 0.11),
                  (cx + R * 0.12, cy - R * 0.18, R * 0.11)],
        "accent": [(cx - R * 0.24 + i * R * 0.26, cy + R * 0.12, R * 0.11)
                   for i in range(len(ACCENTS))],
        "back": (cx - R * 0.23, cy + R * 0.58, R * 0.46, R * 0.18),
    }


def settings_hit(size, x, y):
    """Map a pointer position to a control: (kind, index) | (None, None)."""
    lay = settings_layout(size)
    bx, by, bw, bh = lay["back"]
    if bx <= x <= bx + bw and by <= y <= by + bh:
        return "back", None
    ox, oy, ow, oh = lay["opacity"]
    if ox - oh <= x <= ox + ow + oh and oy - oh <= y <= oy + oh + oh:
        return "opacity", None
    for i, (sx, sy, sr) in enumerate(lay["theme"]):
        if (x - sx) ** 2 + (y - sy) ** 2 <= (sr * 1.6) ** 2:
            return "theme", i
    for i, (sx, sy, sr) in enumerate(lay["accent"]):
        if (x - sx) ** 2 + (y - sy) ** 2 <= (sr * 1.6) ** 2:
            return "accent", i
    return None, None


def opacity_from_x(size, x):
    """Map a pointer x inside the opacity track to an opacity value."""
    ox, oy, ow, oh = settings_layout(size)["opacity"]
    t = (x - ox) / ow
    t = min(1.0, max(0.0, t))
    return round(OPACITY_MIN + t * (OPACITY_MAX - OPACITY_MIN), 3)


def _text(ctx, s, x, y, font_size, color, align="center"):
    ctx.set_source_rgba(*color)
    ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                         cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(font_size)
    ext = ctx.text_extents(s)
    tx = x - ext.width / 2.0 if align == "center" else x
    ty = y + ext.height / 2.0
    ctx.move_to(tx, ty)
    ctx.show_text(s)


def _rrect(ctx, x, y, w, h, r):
    r = min(r, h / 2.0, w / 2.0)
    ctx.new_path()
    ctx.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    ctx.arc(x + w - r, y + r, r, 1.5 * math.pi, 2.0 * math.pi)
    ctx.arc(x + w - r, y + h - r, r, 0.0, 0.5 * math.pi)
    ctx.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    ctx.close_path()


_THEME_SWATCH = {"light": (0.93, 0.94, 0.96), "dark": (0.13, 0.14, 0.18)}


def draw_settings(ctx, size, settings, style):
    """Back face: the settings panel. Cairo-drawn, on-brand, no widgets."""
    cx = cy = size / 2.0
    R = size / 2.0 - 6.0
    lay = settings_layout(size)
    TEXT = (0.95, 0.96, 0.98, 0.95)
    DIM = (0.80, 0.82, 0.88, 0.80)

    # panel (dark translucent disc + rim) — the "back of the card"
    g = cairo.RadialGradient(cx - R * 0.35, cy - R * 0.35, R * 0.1, cx, cy, R)
    g.add_color_stop_rgba(0.0, 0.10, 0.11, 0.16, 0.95)
    g.add_color_stop_rgba(1.0, 0.05, 0.06, 0.10, 0.94)
    ctx.set_source(g)
    ctx.arc(cx, cy, R, 0.0, 2.0 * math.pi)
    ctx.fill()
    ctx.set_line_width(RIM_WIDTH)
    ctx.set_source_rgba(0.90, 0.92, 0.96, 0.70)
    ctx.arc(cx, cy, R, 0.0, 2.0 * math.pi)
    ctx.stroke()

    # title
    _text(ctx, "Settings", cx, cy - R * 0.80, R * 0.16, TEXT)

    # opacity row
    _text(ctx, "Opacity", cx - R * 0.80, cy - R * 0.43, R * 0.11, DIM,
          align="left")
    ox, oy, ow, oh = lay["opacity"]
    frac = (settings.opacity - OPACITY_MIN) / (OPACITY_MAX - OPACITY_MIN)
    ctx.set_source_rgba(1.0, 1.0, 1.0, 0.18)
    _rrect(ctx, ox, oy, ow, oh, oh / 2.0)
    ctx.fill()
    ctx.set_source_rgba(0.90, 0.92, 0.96, 0.9)
    _rrect(ctx, ox, oy, max(oh, ow * frac), oh, oh / 2.0)
    ctx.fill()
    ctx.set_source_rgba(*style.hand_second)
    ctx.arc(ox + ow * frac, oy + oh / 2.0, oh * 1.3, 0.0, 2.0 * math.pi)
    ctx.fill()

    # theme row
    _text(ctx, "Theme", cx - R * 0.80, cy - R * 0.18, R * 0.11, DIM,
          align="left")
    for i, ((sx, sy, sr), name) in enumerate(zip(lay["theme"], THEMES)):
        active = settings.theme == name
        ctx.set_source_rgba(*_THEME_SWATCH[name])
        ctx.arc(sx, sy, sr, 0.0, 2.0 * math.pi)
        ctx.fill()
        if active:
            ctx.set_line_width(max(2.0, sr * 0.22))
            ctx.set_source_rgba(*style.hand_second)
            ctx.arc(sx, sy, sr, 0.0, 2.0 * math.pi)
            ctx.stroke()

    # accent row
    _text(ctx, "Accent", cx - R * 0.80, cy + R * 0.12, R * 0.11, DIM,
          align="left")
    for i, ((sx, sy, sr), key) in enumerate(zip(lay["accent"], ACCENTS)):
        active = settings.accent == key
        rgb = ACCENTS[key]
        ctx.set_source_rgba(rgb[0], rgb[1], rgb[2], 0.95)
        ctx.arc(sx, sy, sr, 0.0, 2.0 * math.pi)
        ctx.fill()
        if active:
            ctx.set_line_width(max(2.0, sr * 0.22))
            ctx.set_source_rgba(1.0, 1.0, 1.0, 0.95)
            ctx.arc(sx, sy, sr, 0.0, 2.0 * math.pi)
            ctx.stroke()

    # back button
    bx, by, bw, bh = lay["back"]
    ctx.set_source_rgba(1.0, 1.0, 1.0, 0.12)
    _rrect(ctx, bx, by, bw, bh, bh / 2.0)
    ctx.fill()
    ctx.set_line_width(1.0)
    ctx.set_source_rgba(1.0, 1.0, 1.0, 0.4)
    _rrect(ctx, bx, by, bw, bh, bh / 2.0)
    ctx.stroke()
    _text(ctx, "\u2039 Back", cx, by + bh / 2.0, R * 0.11, TEXT)

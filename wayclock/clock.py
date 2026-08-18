"""Pure cairo rendering for the analog clock face.

`draw_clock(ctx, size, now)` draws the complete clock (shadow, face, rim,
ticks, hands, center cap) into a cairo context whose coordinate space is a
`size x size` square in logical pixels, with the clock centered. No GTK state
is touched, so the renderer is unit-testable against hand angles.
"""

import math
from datetime import datetime

import cairo

# ---- theme constants (single place to restyle) ----
FACE_CORE = (1.0, 1.0, 1.0, 0.95)      # near-white, translucent
FACE_EDGE = (0.90, 0.92, 0.96, 0.88)
RIM_COLOR = (0.16, 0.18, 0.24, 0.90)
RIM_HIGHLIGHT = (1.0, 1.0, 1.0, 0.55)
TICK_HOUR = (0.12, 0.14, 0.20, 0.95)
TICK_MINUTE = (0.12, 0.14, 0.20, 0.35)
HAND_HOUR = (0.12, 0.14, 0.20, 0.95)
HAND_MINUTE = (0.14, 0.16, 0.22, 0.95)
HAND_SECOND = (0.85, 0.27, 0.22, 0.95)
SHADOW = (0.0, 0.0, 0.0, 0.16)

SHADOW_OFFSET = 3.0
RIM_WIDTH = 2.5
HOUR_TICK_W = 4.0
MINUTE_TICK_W = 1.5
HAND_W = {"hour": 7.0, "minute": 5.0, "second": 2.0}
HAND_LEN = {"hour": 0.45, "minute": 0.60, "second": 0.72}
SECOND_TAIL = 0.15
CAP_R = 5.0
CAP_ACCENT_R = 2.5


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


def _shadow_disk(ctx, cx, cy, R):
    r = R + 4.0
    g = cairo.RadialGradient(
        cx + SHADOW_OFFSET, cy + SHADOW_OFFSET, R * 0.5,
        cx + SHADOW_OFFSET, cy + SHADOW_OFFSET, r,
    )
    g.add_color_stop_rgba(0.0, *SHADOW)
    g.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
    ctx.set_source(g)
    ctx.arc(cx + SHADOW_OFFSET, cy + SHADOW_OFFSET, r, 0.0, 2.0 * math.pi)
    ctx.fill()


def _face(ctx, cx, cy, R):
    g = cairo.RadialGradient(cx - R * 0.35, cy - R * 0.35, R * 0.1, cx, cy, R)
    g.add_color_stop_rgba(0.0, *FACE_CORE)
    g.add_color_stop_rgba(1.0, *FACE_EDGE)
    ctx.set_source(g)
    ctx.arc(cx, cy, R, 0.0, 2.0 * math.pi)
    ctx.fill()


def _rim(ctx, cx, cy, R):
    ctx.set_line_width(RIM_WIDTH)
    ctx.set_source_rgba(*RIM_COLOR)
    ctx.arc(cx, cy, R, 0.0, 2.0 * math.pi)
    ctx.stroke()
    ctx.set_line_width(1.0)
    ctx.set_source_rgba(*RIM_HIGHLIGHT)
    ctx.arc(cx, cy, R - RIM_WIDTH, 0.0, 2.0 * math.pi)
    ctx.stroke()


def _ticks(ctx, cx, cy, R):
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    for i in range(60):
        deg = i * 6.0
        if i % 5 == 0:
            x1, y1 = _polar(cx, cy, R * 0.76, deg)
            x2, y2 = _polar(cx, cy, R * 0.90, deg)
            ctx.set_line_width(HOUR_TICK_W)
            ctx.set_source_rgba(*TICK_HOUR)
        else:
            x1, y1 = _polar(cx, cy, R * 0.83, deg)
            x2, y2 = _polar(cx, cy, R * 0.90, deg)
            ctx.set_line_width(MINUTE_TICK_W)
            ctx.set_source_rgba(*TICK_MINUTE)
        ctx.move_to(x1, y1)
        ctx.line_to(x2, y2)
        ctx.stroke()


def _hand(ctx, cx, cy, R, deg, length, width, color, tail=0.0):
    x0, y0 = _polar(cx, cy, -R * tail, deg)   # tail extends behind the center
    x1, y1 = _polar(cx, cy, R * length, deg)
    ctx.save()
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_width(width)
    ctx.set_source_rgba(*SHADOW)
    ctx.move_to(x0 + SHADOW_OFFSET, y0 + SHADOW_OFFSET)
    ctx.line_to(x1 + SHADOW_OFFSET, y1 + SHADOW_OFFSET)
    ctx.stroke()
    ctx.set_source_rgba(*color)
    ctx.move_to(x0, y0)
    ctx.line_to(x1, y1)
    ctx.stroke()
    ctx.restore()


def _cap(ctx, cx, cy):
    ctx.set_source_rgba(*HAND_MINUTE)
    ctx.arc(cx, cy, CAP_R, 0.0, 2.0 * math.pi)
    ctx.fill()
    ctx.set_source_rgba(*HAND_SECOND)
    ctx.arc(cx, cy, CAP_ACCENT_R, 0.0, 2.0 * math.pi)
    ctx.fill()


def draw_face(ctx, size):
    """Static part: shadow, face, rim, ticks. Safe to cache offscreen."""
    cx = cy = size / 2.0
    R = size / 2.0 - 6.0
    _shadow_disk(ctx, cx, cy, R)
    _face(ctx, cx, cy, R)
    _rim(ctx, cx, cy, R)
    _ticks(ctx, cx, cy, R)


def draw_hands(ctx, size, now):
    """Time-dependent part: the three hands and the center cap."""
    cx = cy = size / 2.0
    R = size / 2.0 - 6.0
    h_a, m_a, s_a = hand_angles(now)
    _hand(ctx, cx, cy, R, h_a, HAND_LEN["hour"], HAND_W["hour"], HAND_HOUR)
    _hand(ctx, cx, cy, R, m_a, HAND_LEN["minute"], HAND_W["minute"], HAND_MINUTE)
    _hand(ctx, cx, cy, R, s_a, HAND_LEN["second"], HAND_W["second"], HAND_SECOND,
          tail=SECOND_TAIL)
    _cap(ctx, cx, cy)


def draw_clock(ctx, size, now):
    """Render the full clock. `now` is a datetime with microsecond precision."""
    draw_face(ctx, size)
    draw_hands(ctx, size, now)

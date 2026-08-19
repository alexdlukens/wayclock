"""GTK3 window, transparency, flip card, and frame-clock pacing for wayclock."""

import math
import sys
import time
from datetime import datetime

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from . import clock, settings as settings_mod

SIZE = 320                       # default logical px, square
MIN_SIZE = 64                    # smallest square we'll render
REDRAW_INTERVAL = 1.0 / 60.0     # sweep the second hand at ~60 fps
FLIP_DURATION = 0.25             # seconds for a full flip


def _configure_layer_shell(window):
    """Try the (optional) gtk-layer-shell overlay; False = plain window."""
    try:
        import gtk_layer_shell
    except ImportError:
        return False
    gtk_layer_shell.init_for_window(window)
    gtk_layer_shell.set_layer(window, gtk_layer_shell.Layer.TOP)
    gtk_layer_shell.set_anchor(
        window, gtk_layer_shell.Edge.TOP | gtk_layer_shell.Edge.RIGHT)
    gtk_layer_shell.set_margin(window, 24)
    gtk_layer_shell.set_exclusive_zone(window, -1)   # floats, reserves nothing
    gtk_layer_shell.set_keyboard_mode(window, gtk_layer_shell.KeyboardMode.NONE)
    return True


class ClockWindow:
    def __init__(self):
        self.settings = settings_mod.load()
        self.style = clock.style_from(self.settings)

        self.win = Gtk.Window()
        self.win.set_default_size(SIZE, SIZE)
        self.win.set_resizable(True)

        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is None:
            print("warning: compositor has no RGBA visual; clock background "
                  "will be opaque", file=sys.stderr)
        else:
            self.win.set_visual(visual)
        self.win.set_app_paintable(True)
        self.win.connect("draw", self.on_window_draw)
        self.win.connect("destroy", Gtk.main_quit)

        _configure_layer_shell(self.win)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(MIN_SIZE, MIN_SIZE)
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.connect("draw", self.on_draw)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.area.connect("button-press-event", self.on_button_press)
        self.area.connect("button-release-event", self.on_button_release)
        self.area.connect("motion-notify-event", self.on_motion)

        # The clock content is always square (largest square that fits,
        # centered); any extra window margin stays transparent, so the visible
        # clock is square/circular at every window size.
        aspect = Gtk.AspectFrame(xalign=0.5, yalign=0.5, ratio=1.0,
                                 obey_child=False)
        aspect.set_shadow_type(Gtk.ShadowType.NONE)
        aspect.add(self.area)
        self.win.add(aspect)

        self._face_cache = None
        self._face_scale = 0
        self._face_size = 0
        self._last_draw = 0.0

        # flip state: 1.0 = front (clock), -1.0 = back (settings)
        self._flip = 1.0
        self._flip_target = 1.0
        self._flipping = False
        self._flip_from = 1.0
        self._flip_start = 0.0
        self._drag = None

        self.win.add_tick_callback(self.on_tick, None)
        self.win.show_all()
        self.win.set_title("wayclock")

    # ---- sizing ----

    def _current_size(self):
        """Logical side of the square content (DrawingArea allocation)."""
        alloc = self.area.get_allocation()
        return max(MIN_SIZE, min(alloc.width, alloc.height))

    def _face_surface(self, scale, size):
        """Device-resolution offscreen face; rebuilt on scale/size/style change."""
        if (self._face_cache is None or self._face_scale != scale
                or self._face_size != size):
            px = int(round(size * scale))
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
            ctx = cairo.Context(surf)
            ctx.scale(scale, scale)
            clock.draw_face(ctx, size, self.style)
            self._face_cache = surf
            self._face_scale = scale
            self._face_size = size
        return self._face_cache

    def _invalidate_face(self):
        self._face_cache = None
        self.style = clock.style_from(self.settings)

    # ---- flip ----

    def _flip_toward(self, target):
        if self._flipping:
            return
        self._flip_target = target
        self._flip_from = self._flip
        self._flip_start = time.monotonic()
        self._flipping = True
        self.area.queue_draw()

    # ---- input ----

    def _apply_opacity_from_x(self, x, size):
        self.settings.opacity = clock.opacity_from_x(size, x)
        self._invalidate_face()
        self.area.queue_draw()

    def _apply_frost_from_x(self, x, size):
        self.settings.frost = clock.frost_from_x(size, x)
        self._invalidate_face()
        self.area.queue_draw()

    def _save_settings(self):
        settings_mod.save(self.settings)

    def on_button_press(self, widget, event):
        if event.button != 1 or self._flipping:
            return False
        size = self._current_size()
        x, y = event.x, event.y
        if self._flip > 0:                       # front: only the gear responds
            if clock.gear_hit(size, x, y):
                self._flip_toward(-1.0)          # open settings
            return True
        # back face: settings controls
        kind, idx = clock.settings_hit(size, x, y)
        if kind == "back":
            self._flip_toward(1.0)               # close settings
        elif kind == "opacity":
            self._drag = "opacity"
            self._apply_opacity_from_x(x, size)
        elif kind == "frost":
            self._drag = "frost"
            self._apply_frost_from_x(x, size)
        elif kind == "theme":
            self.settings.theme = settings_mod.THEMES[idx]
            self._invalidate_face()
            self.area.queue_draw()
            self._save_settings()
        elif kind == "accent":
            self.settings.accent = list(settings_mod.ACCENTS)[idx]
            self._invalidate_face()
            self.area.queue_draw()
            self._save_settings()
        return True

    def on_motion(self, widget, event):
        if self._drag in ("opacity", "frost"):
            size = self._current_size()
            if self._drag == "opacity":
                self._apply_opacity_from_x(event.x, size)
            else:
                self._apply_frost_from_x(event.x, size)
        return False

    def on_button_release(self, widget, event):
        if self._drag in ("opacity", "frost"):
            self._drag = None
            self._save_settings()
        return False

    # ---- frame clock ----

    def on_tick(self, widget, frame_clock, user_data):
        now = time.monotonic()
        if self._flipping:
            t = (now - self._flip_start) / FLIP_DURATION
            if t >= 1.0:
                self._flip = self._flip_target
                self._flipping = False
            else:
                e = 0.5 - 0.5 * math.cos(math.pi * t)   # ease-in-out
                self._flip = self._flip_from + (self._flip_target
                                                - self._flip_from) * e
            widget.queue_draw()
        elif self._flip > 0 and now - self._last_draw >= REDRAW_INTERVAL:
            widget.queue_draw()
        return True  # keep ticking

    # ---- drawing ----

    def on_window_draw(self, widget, cr):
        # Keep any window margin (outside the square content) transparent.
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.paint()
        return False

    def on_draw(self, widget, cr):
        # Transparent square background first (only the circle is visible).
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        size = self._current_size()
        scale = cr.get_matrix().xx
        flip = self._flip

        cr.save()
        if abs(flip) < 1.0:                 # mid-flip: squash along X
            cx = size / 2.0
            cr.translate(cx, 0.0)
            cr.scale(abs(flip), 1.0)
            cr.translate(-cx, 0.0)
        if flip > 0:                        # front: clock
            cr.save()
            cr.scale(1.0 / scale, 1.0 / scale)
            cr.set_source_surface(self._face_surface(scale, size), 0, 0)
            cr.paint()
            cr.restore()
            clock.draw_hands(cr, size, datetime.now(), self.style)
        else:                               # back: settings
            clock.draw_settings(cr, size, self.settings, self.style)
        cr.restore()

        self._last_draw = time.monotonic()
        return False


def main():
    print("wayclock: started", file=sys.stderr, flush=True)
    Gtk.init(sys.argv)
    ClockWindow()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

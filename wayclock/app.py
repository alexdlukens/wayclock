#!/usr/bin/env python3
"""GTK3 window, transparency, and frame-clock pacing for wayclock."""

import sys
import time
from datetime import datetime

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import clock

SIZE = 320                       # logical px, square
REDRAW_INTERVAL = 1.0 / 60.0     # sweep the second hand at ~60 fps


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
        self.win = Gtk.Window()
        self.win.set_default_size(SIZE, SIZE)
        self.win.set_resizable(False)

        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is None:
            print("warning: compositor has no RGBA visual; clock background "
                  "will be opaque", file=sys.stderr)
        else:
            self.win.set_visual(visual)
        self.win.set_app_paintable(True)
        self.win.connect("destroy", Gtk.main_quit)

        _configure_layer_shell(self.win)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(SIZE, SIZE)
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.connect("draw", self.on_draw)
        self.win.add(self.area)

        self._face_cache = None
        self._face_scale = 0
        self._last_draw = 0.0
        self.win.add_tick_callback(self.on_tick, None)
        self.win.show_all()
        self.win.set_title("wayclock")

    def _face_surface(self, scale):
        """Device-resolution offscreen face; rebuilt when the draw scale changes."""
        if self._face_cache is None or self._face_scale != scale:
            size = int(round(SIZE * scale))
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            ctx = cairo.Context(surf)
            ctx.scale(scale, scale)
            clock.draw_face(ctx, SIZE)
            self._face_cache = surf
            self._face_scale = scale
        return self._face_cache

    def on_tick(self, widget, frame_clock, user_data):
        """Frame-clock (vsync) callback: redraw at most every 1/60 s."""
        now = time.monotonic()
        if now - self._last_draw >= REDRAW_INTERVAL:
            widget.queue_draw()
        return True  # keep ticking

    def on_draw(self, widget, cr):
        # Transparent window background first (only the circle is visible).
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        # Cached static face, in the same (widget-local) coordinate space as the
        # hands so both stay centered on each other. scale(1/scale) maps cache
        # pixels to logical pixels: crisp at any draw scale.
        scale = cr.get_matrix().xx
        cr.save()
        cr.scale(1.0 / scale, 1.0 / scale)
        cr.set_source_surface(self._face_surface(scale), 0, 0)
        cr.paint()
        cr.restore()
        # Time-dependent hands on top.
        clock.draw_hands(cr, SIZE, datetime.now())
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

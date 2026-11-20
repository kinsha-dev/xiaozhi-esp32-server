"""Floating avatar overlay (AppKit): a light, flat face that listens, thinks and talks (mouth follows the speaker's output
level, eyes blink), with live captions under it. Shown while vibi is active."""
import math
import time

from mac_app import captions

BARS = 28  # kept for the old name; the level history now only smooths the mouth
W, H = 460, 120
LINE_H = 17
MAX_LINES = 3
CHARS_PER_LINE = 62  # ~13pt text across the caption width


class Overlay:
    """Create and use on the main thread only (call update() from a rumps.Timer)."""

    def __init__(self, level_fn, mic_fn=lambda: 0.0):
        self._level_fn = level_fn
        self._mic_fn = mic_fn
        self._color = (0.35, 0.8, 1.0)
        self._panel = None
        self._view = None
        self._levels = [0.0] * BARS
        self._shown = False
        self._mouth = 0.0
        self._look = (0.0, 0.0)
        self._blink = False
        self._label = None
        self._cap = False
        self._cap_key = None

    def _build(self):
        import AppKit
        import objc
        from Foundation import NSMakeRect

        levels = self._levels
        outer = self

        class WaveView(AppKit.NSView):
            def drawRect_(self, _rect):
                b = self.bounds()
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.82).set()
                AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 18, 18).fill()
                cx, cy, r = b.size.width / 2, b.size.height - H / 2, 40.0  # face sits in the top strip
                R, G, B = outer._color
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(R, G, B, 0.22).set()
                AppKit.NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r - 4, cy - r - 4, 2 * r + 8, 2 * r + 8)).fill()
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(R, G, B, 1.0).set()
                AppKit.NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r, cy - r, 2 * r, 2 * r)).fill()
                dark = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.1, 1.0)
                dark.set()
                ex, ey = outer._look
                eh = 3.0 if outer._blink else 11.0  # blink = squashed eyes
                for sx in (-15, 15):
                    AppKit.NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx + sx - 5 + ex, cy + 8 + ey - eh / 2, 10, eh)).fill()
                m = outer._mouth  # 0 closed .. 1 wide open
                mw, mh = 22 + 8 * m, 3 + 17 * m
                path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(cx - mw / 2, cy - 22 - mh / 2, mw, mh), min(mh / 2, 9), min(mh / 2, 9))
                path.fill()

        scr = AppKit.NSScreen.mainScreen().frame()
        rect = NSMakeRect(scr.origin.x + (scr.size.width - W) / 2, scr.origin.y + 90, W, H)
        p = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        p.setLevel_(AppKit.NSStatusWindowLevel)
        p.setOpaque_(False)
        p.setBackgroundColor_(AppKit.NSColor.clearColor())
        p.setHasShadow_(True)
        p.setIgnoresMouseEvents_(True)
        p.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        view = WaveView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        p.setContentView_(view)
        label = AppKit.NSTextField.alloc().initWithFrame_(NSMakeRect(16, 8, W - 32, LINE_H * MAX_LINES))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAlignment_(AppKit.NSTextAlignmentCenter)
        label.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        label.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        label.setMaximumNumberOfLines_(MAX_LINES)
        label.cell().setWraps_(True)
        label.setHidden_(True)
        view.addSubview_(label)
        self._panel, self._view, self._label = p, view, label

    def update(self, state: str, muted: bool = False):
        """state: controller state. Visible for every non-idle state; bars follow output level when speaking."""
        want = state in ("connecting", "thinking", "speaking", "listening") and not muted
        if want and not self._shown:
            if self._panel is None:
                self._build()
            self._panel.orderFrontRegardless()
            self._shown = True
        elif not want and self._shown:
            self._panel.orderOut_(None)
            self._shown = False
            self._mouth = 0.0
            return
        if not self._shown:
            return
        t = time.monotonic()
        # speaking: blue face, jaw follows the output level; listening: green, mouth reacts to your voice, eyes wide;
        # connecting/thinking: amber, eyes glance up and to the side, mouth small.
        self._color = {"speaking": (0.35, 0.8, 1.0), "listening": (0.3, 0.9, 0.45)}.get(state, (1.0, 0.75, 0.3))
        sp = self._level_fn() if state == "speaking" else 0.0
        mic = min(1.0, self._mic_fn() * 12) if state == "listening" else 0.0
        if state == "speaking":
            target = min(1.0, sp * 4) * (0.4 + 0.6 * abs(math.sin(t * 11)))
        elif state == "listening":
            target = 0.06 + mic * 0.6
        else:
            target = 0.12
        self._mouth += (target - self._mouth) * 0.5
        self._blink = (t % 3.6) < 0.12
        self._look = (4.0, 4.0) if state in ("thinking", "connecting") else (0.0, 0.0)
        self._caption()
        self._view.setNeedsDisplay_(True)

    def _caption(self):
        """Show the latest line (yours dim, vibi's light blue) under the bars; grow the panel only while it shows."""
        import AppKit
        from Foundation import NSMakeRect
        role, text = captions.current()
        want = bool(text)
        lines = min(MAX_LINES, max(1, -(-len(text) // CHARS_PER_LINE))) if want else 0
        if text and len(text) > MAX_LINES * CHARS_PER_LINE:  # more than fits: keep the newest part
            text = "…" + text[-(MAX_LINES * CHARS_PER_LINE - 1):]
        if (want, lines) != (self._cap, getattr(self, "_lines", 0)):
            f = self._panel.frame()
            h = H + (LINE_H * lines + 14 if want else 0)
            self._panel.setFrame_display_(NSMakeRect(f.origin.x, f.origin.y, W, h), True)
            self._view.setFrame_(NSMakeRect(0, 0, W, h))
            self._label.setFrame_(NSMakeRect(16, 8, W - 32, LINE_H * lines + 2))
            self._label.setHidden_(not want)
            self._cap, self._lines = want, lines
        if want and (role, text) != self._cap_key:
            self._cap_key = (role, text)
            self._label.setStringValue_(text)
            col = (AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.7, 0.9, 1.0, 1.0) if role == "vibi"
                   else AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.75, 1.0))
            self._label.setTextColor_(col)

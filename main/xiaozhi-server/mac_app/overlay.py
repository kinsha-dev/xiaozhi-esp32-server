"""Floating wave overlay (AppKit): shown while vibi is responding, driven by the speaker's output level."""
import math
import time

BARS = 28
W, H = 260, 64


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
                gap = 4.0
                bw = (b.size.width - 32 - gap * (BARS - 1)) / BARS
                mid = b.size.height / 2
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*outer._color, 1.0).set()
                for i, lv in enumerate(levels):
                    h = 4 + lv * (b.size.height - 16)
                    r = NSMakeRect(16 + i * (bw + gap), mid - h / 2, bw, h)
                    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, bw / 2, bw / 2).fill()

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
        self._panel, self._view = p, view

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
            self._levels[:] = [0.0] * BARS
            return
        if not self._shown:
            return
        t = time.monotonic()
        # speaking: blue ripple driven by output level; listening: green bars rising from the centre with the
        # live mic level (reacts to your voice); connecting/thinking: dim amber pulse.
        self._color = {"speaking": (0.35, 0.8, 1.0), "listening": (0.3, 0.9, 0.45)}.get(state, (1.0, 0.75, 0.3))
        sp = self._level_fn() if state == "speaking" else 0.0
        mic = min(1.0, self._mic_fn() * 12) if state == "listening" else 0.0
        for i in range(BARS):
            ripple = 0.5 + 0.5 * math.sin(t * 9 + i * 0.7)
            if state == "speaking":
                target = min(1.0, sp * 4) * (0.35 + 0.65 * ripple)
            elif state == "listening":
                edge = 1.0 - abs(i - (BARS - 1) / 2) / (BARS / 2)  # tallest in the middle
                target = 0.06 + mic * (0.3 + 0.7 * edge) * (0.6 + 0.4 * ripple)
            else:
                target = 0.08 + 0.07 * ripple
            self._levels[i] += (target - self._levels[i]) * 0.5
        self._view.setNeedsDisplay_(True)

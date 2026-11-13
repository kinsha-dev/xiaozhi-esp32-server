import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mac_app.controller import Controller


class _Mic:
    started = stopped = 0

    def start(self): self.started += 1
    def stop(self): self.stopped += 1


def make(d):
    mic = _Mic()
    c = Controller(SimpleNamespace(data_dir=Path(d)), None, None, lambda cb: mic, None, lambda t: None)
    return c, mic


class DeviceTests(unittest.TestCase):
    def test_modes_and_board_state(self):
        with tempfile.TemporaryDirectory() as d:
            c, mic = make(d)
            self.assertEqual(c.device_mode(), "auto")
            self.assertFalse(c.defer_to_board())
            (Path(d) / "board_connected").write_text(f"{os.getpid()} 1")
            (Path(d) / "board_heartbeat").write_text(f"{time.time():.0f}")
            self.assertTrue(c.defer_to_board())
            (Path(d) / "board_state").write_text(f"{time.time()} speaking")
            self.assertEqual(c.display_state(), "speaking")
            self.assertTrue(c.mirroring)
            (Path(d) / "board_state").write_text(f"{time.time() - 10} speaking")
            self.assertEqual(c.display_state(), "idle")
            c.set_device_mode("mac")
            self.assertFalse(c.defer_to_board())
            c.set_device_mode("board")
            self.assertTrue(c.defer_to_board())
            self.assertEqual(mic.stopped, 1)

    def test_x_when_no_heartbeat_for_five_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = make(d)
            self.assertEqual(c.display_state(), "offline")  # no heartbeat file yet
            (Path(d) / "board_heartbeat").write_text(f"{time.time() - 100:.0f}")
            self.assertEqual(c.display_state(), "idle")
            (Path(d) / "board_heartbeat").write_text(f"{time.time() - 301:.0f}")
            self.assertEqual(c.display_state(), "offline")
            c.set_device_mode("mac")  # the board is not in use, so its silence does not matter
            self.assertEqual(c.display_state(), "idle")


    def test_board_stays_first_even_with_earphones(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = make(d)
            (Path(d) / "board_connected").write_text(f"{os.getpid()} 1")
            self.assertTrue(c.defer_to_board())
            self.assertTrue(c.update_earphones(True, False))  # headset is the mic, quiet Mac: changed
            self.assertTrue(c.defer_to_board())  # the connected board still wins in auto
            self.assertFalse(c.update_earphones(True, False))  # nothing new
            self.assertTrue(c.update_earphones(True, True))  # a video starts
            self.assertTrue(c.defer_to_board())
            self.assertFalse(c.update_earphones(False, False))
            c.set_device_mode("board")  # a forced board is never taken over
            self.assertFalse(c.update_earphones(True, False))
            self.assertTrue(c.defer_to_board())

    def test_no_x_while_earphones_are_in_use(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = make(d)
            self.assertEqual(c.display_state(), "offline")
            c.update_earphones(True, False)
            self.assertEqual(c.display_state(), "idle")


if __name__ == "__main__":
    unittest.main()


def test_wake_command_is_what_follows_the_wake_phrase():
    from mac_app.wake import wake_command
    assert wake_command("Hi Vibi, what time is it?") == "what time is it"
    assert wake_command("hi bee bee play music") == "play music"
    assert wake_command("Hi bibi") == ""
    assert wake_command("what time is it") == ""

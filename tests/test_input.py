import os
import time
import unittest

from lazyviewer import input as input_mod


class ReadKeyRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        input_mod._PENDING_BYTES.clear()

    def tearDown(self) -> None:
        input_mod._PENDING_BYTES.clear()

    def test_single_escape_returns_esc_without_second_keypress(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1b")
            started = time.monotonic()
            key = input_mod.read_key(read_fd, timeout_ms=20)
            elapsed = time.monotonic() - started
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "ESC")
        # Esc waits briefly for sequence bytes, but should not require another key press.
        self.assertLess(elapsed, 0.2)

    def test_arrow_sequence_is_still_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1b[A")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "UP")

    def test_escape_does_not_swallow_following_printable_key(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1ba")
            first = input_mod.read_key(read_fd, timeout_ms=20)
            second = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(first, "ESC")
        self.assertEqual(second, "a")

    def test_ctrl_k_is_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x0b")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "CTRL_K")

    def test_ctrl_d_is_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x04")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "CTRL_D")


if __name__ == "__main__":
    unittest.main()

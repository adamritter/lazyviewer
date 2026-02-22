"""Regression tests for raw-key decoding.

Covers ESC timing, arrow/meta sequences, and control-key token mapping.
These tests protect interactive input handling in raw terminal mode.
"""

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

    def test_ctrl_g_is_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x07")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "CTRL_G")

    def test_ctrl_o_is_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x0f")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "CTRL_O")

    def test_ctrl_question_is_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1f")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "CTRL_QUESTION")

    def test_alt_left_sequence_is_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1b[1;3D")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "ALT_LEFT")

    def test_alt_right_sequence_is_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1b[1;3C")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "ALT_RIGHT")

    def test_meta_word_shortcuts_map_to_alt_left_right(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1bb\x1bf")
            first = input_mod.read_key(read_fd, timeout_ms=20)
            second = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(first, "ALT_LEFT")
        self.assertEqual(second, "ALT_RIGHT")

    def test_sgr_mouse_drag_event_maps_to_left_down(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1b[<32;15;7M")
            key = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(key, "MOUSE_LEFT_DOWN:15:7")

    def test_sgr_mouse_wheel_left_and_right_are_recognized(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1b[<66;10;4M\x1b[<67;11;5M")
            left = input_mod.read_key(read_fd, timeout_ms=20)
            right = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(left, "MOUSE_WHEEL_LEFT:10:4")
        self.assertEqual(right, "MOUSE_WHEEL_RIGHT:11:5")

    def test_sgr_mouse_wheel_with_shift_modifier_still_maps_to_vertical_wheel(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            # 64/65 wheel events plus shift modifier bit (4) -> 68/69.
            os.write(write_fd, b"\x1b[<68;12;6M\x1b[<69;13;7M")
            up = input_mod.read_key(read_fd, timeout_ms=20)
            down = input_mod.read_key(read_fd, timeout_ms=20)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        self.assertEqual(up, "MOUSE_WHEEL_UP:12:6")
        self.assertEqual(down, "MOUSE_WHEEL_DOWN:13:7")


if __name__ == "__main__":
    unittest.main()

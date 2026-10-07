"""DMX out of a USB interface: the Enttec DMX USB Pro protocol.

Most USB DMX boxes speak it (Enttec DMX USB Pro / Pro Mk2, DMXking
ultraDMX, many clones): the computer sees a serial port (COM3,
/dev/ttyUSB0) and each frame is one message -

    0x7E, label 6 (send DMX), length LSB, length MSB,
    0x00 (start code) + up to 512 channel bytes, 0xE7

The box makes the DMX signal itself, so no timing is needed here.  One
box is one universe: universe 1 goes out, others are counted and left.
Standard library only: ctypes on Windows, termios elsewhere.  Same
interface as ArtNetSender (send / sync / stats / close / dry_run), so the
engine's output loop doesn't change.
"""
from __future__ import annotations

import glob
import os
import sys
import time

START, END, LABEL_SEND_DMX = 0x7E, 0xE7, 6
RETRY_S = 2.0          # unplugged: try the port again this often


def build_frame(data) -> bytes:
    """One 'send DMX' message for 512 (or fewer) channels."""
    body = bytes([0]) + bytes(data[:512])
    n = len(body)
    return bytes([START, LABEL_SEND_DMX, n & 0xFF, n >> 8]) + body + bytes([END])


def list_ports() -> list[str]:
    """The computer's serial ports a USB DMX box shows up as."""
    if sys.platform == "win32":
        try:
            import winreg
            out = []
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    out.append(str(winreg.EnumValue(k, i)[1]))
            return sorted(out, key=lambda p: int(p[3:]) if p[3:].isdigit() else 999)
        except OSError:
            return []
    pats = ["/dev/cu.usbserial*", "/dev/cu.usbmodem*"] if sys.platform == "darwin" else \
        ["/dev/ttyUSB*", "/dev/ttyACM*"]
    return sorted(p for pat in pats for p in glob.glob(pat))


def valid_port(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"COM\d{1,3}|/dev/[\w./-]{1,60}", name or ""))


class _Port:
    """A serial port opened for writing, raw."""

    def __init__(self, name: str) -> None:
        self.name = name
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateFileW.restype = wintypes.HANDLE
            h = k32.CreateFileW("\\\\.\\" + name, 0x40000000, 0, None, 3, 0, None)   # GENERIC_WRITE, OPEN_EXISTING
            if h in (None, wintypes.HANDLE(-1).value):
                raise OSError(f"can't open {name} (error {ctypes.get_last_error()}) - is the interface plugged in, "
                              "and not open in another program?")

            class _Timeouts(ctypes.Structure):
                _fields_ = [(f, wintypes.DWORD) for f in ("ReadIntervalTimeout", "ReadTotalTimeoutMultiplier",
                                                         "ReadTotalTimeoutConstant", "WriteTotalTimeoutMultiplier",
                                                         "WriteTotalTimeoutConstant")]
            k32.SetCommTimeouts(h, ctypes.byref(_Timeouts(0, 0, 0, 0, 100)))   # never block the output
            self._k32, self._h, self._ct = k32, h, ctypes
        else:
            import termios
            import tty
            fd = os.open(name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                tty.setraw(fd)
                attrs = termios.tcgetattr(fd)
                attrs[4] = attrs[5] = termios.B115200      # ignored by the box's USB chip; set anyway
                termios.tcsetattr(fd, termios.TCSANOW, attrs)
            except termios.error:
                pass                                       # not a tty (a test pipe): still writable
            self._fd = fd

    def write(self, data: bytes) -> None:
        if sys.platform == "win32":
            n = self._ct.c_ulong(0)
            if not self._k32.WriteFile(self._h, data, len(data), self._ct.byref(n), None) or n.value != len(data):
                raise OSError(f"{self.name}: write failed (error {self._ct.get_last_error()})")
        else:
            view = memoryview(data)
            while view:
                try:
                    view = view[os.write(self._fd, view):]
                except BlockingIOError:
                    raise OSError(f"{self.name}: the interface isn't taking data") from None

    def close(self) -> None:
        try:
            if sys.platform == "win32":
                self._k32.CloseHandle(self._h)
            else:
                os.close(self._fd)
        except OSError:
            pass


class UsbProSender:
    """Sends frames to a USB DMX Pro box.  dry_run counts, touches nothing."""

    transport = "usbpro"

    def __init__(self, port_name: str, dry_run: bool = True) -> None:
        self.host = port_name
        self.port = 0
        self.dry_run = bool(dry_run)
        self.frames_sent = 0
        self.simulated_frames = 0
        self.skipped_universes = 0
        self.errors = 0
        self.last_error: str | None = None
        self._port: _Port | None = None
        self._retry_at = 0.0

    def _open(self) -> _Port | None:
        if self._port is None and time.monotonic() >= self._retry_at:
            try:
                self._port = _Port(self.host)
            except OSError as exc:
                self._fail(exc)
        return self._port

    def _fail(self, exc: Exception) -> None:
        self.errors += 1
        if self.last_error != str(exc):
            print(f"[usbdmx] {exc}")
        self.last_error = str(exc)
        self._retry_at = time.monotonic() + RETRY_S
        if self._port is not None:
            self._port.close()
            self._port = None

    def send(self, universe: int, data) -> bool:
        if universe != 1:                      # the patch's universes count from 1
            self.skipped_universes += 1        # one box, one universe: the rest
            return True                        # are left out, not an error
        if self.dry_run:
            self.simulated_frames += 1
            return False
        port = self._open()
        if port is None:
            return False
        try:
            port.write(build_frame(data))
        except OSError as exc:
            self._fail(exc)                     # unplugged: try again in a moment
            return False
        self.frames_sent += 1
        self.last_error = None
        return True

    def sync(self, universes: int) -> bool:
        return False

    def stats(self) -> dict:
        return {"transport": "usbpro", "host": self.host, "frames_sent": self.frames_sent,
                "simulated_frames": self.simulated_frames, "errors": self.errors,
                "skipped_universes": self.skipped_universes, "last_error": self.last_error}

    def close(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None

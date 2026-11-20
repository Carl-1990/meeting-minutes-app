#!/usr/bin/env python3
"""Draw a simple microphone icon and pack icon.png + icon.icns."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _in_round_rect(x: int, y: int, size: int, radius: int) -> bool:
    if radius <= 0:
        return True
    left, right = radius, size - 1 - radius
    top, bottom = radius, size - 1 - radius
    if left <= x <= right or top <= y <= bottom:
        return True
    dx = left - x if x < left else x - right
    dy = top - y if y < top else y - bottom
    return dx * dx + dy * dy <= radius * radius


def _set(buf: bytearray, size: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if 0 <= x < size and 0 <= y < size:
        i = (y * size + x) * 4
        buf[i : i + 4] = bytes(color)


def draw(size: int) -> bytes:
    buf = bytearray(size * size * 4)
    radius = max(2, int(size * 0.22))
    green = (46, 125, 50, 255)
    white = (255, 255, 255, 255)
    for y in range(size):
        for x in range(size):
            if _in_round_rect(x, y, size, radius):
                _set(buf, size, x, y, green)
    cx = size // 2
    cy = int(size * 0.40)
    rx = max(2, int(size * 0.14))
    ry = max(3, int(size * 0.20))
    for y in range(cy - ry, cy + ry + 1):
        for x in range(cx - rx, cx + rx + 1):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                _set(buf, size, x, y, white)
    stem_w = max(2, size // 28)
    stem_top = cy + int(ry * 0.2)
    stem_bottom = int(size * 0.72)
    for y in range(stem_top, stem_bottom):
        for x in range(cx - stem_w, cx + stem_w):
            _set(buf, size, x, y, white)
    base_w = max(6, int(size * 0.22))
    base_h = max(2, size // 28)
    for y in range(stem_bottom, stem_bottom + base_h):
        for x in range(cx - base_w, cx + base_w):
            _set(buf, size, x, y, white)
    return bytes(buf)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_bytes(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * size * 4 : (y + 1) * size * 4] for y in range(size))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def icns_bytes(images: list[tuple[bytes, bytes]]) -> bytes:
    body = b""
    for kind, png in images:
        body += kind + struct.pack(">I", len(png) + 8) + png
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main() -> None:
    rendered = {size: draw(size) for size in (64, 128, 256, 512)}
    pngs = {size: png_bytes(size, rendered[size]) for size in rendered}
    (ROOT / "icon.png").write_bytes(pngs[256])
    icns = icns_bytes(
        [
            (b"ic11", pngs[64]),    # 32 pt @2x
            (b"ic12", pngs[128]),   # 64 pt @2x
            (b"ic07", pngs[128]),
            (b"ic08", pngs[256]),
            (b"ic13", pngs[512]),   # 256 pt @2x
            (b"ic09", pngs[512]),
        ]
    )
    (ROOT / "icon.icns").write_bytes(icns)
    print(f"wrote {ROOT / 'icon.png'} and {ROOT / 'icon.icns'}")


if __name__ == "__main__":
    main()

"""Draws icon.png, the extension's icon: a route tree on a dark tile, one route amber like a
finding. Standard library only. Run from this folder: python3 make_icon.py"""

import math
import struct
import zlib

SIZE = 256
SAMPLES = 4  # per axis, for anti-aliasing

TILE_TOP, TILE_BOTTOM = (40, 56, 120), (20, 28, 66)
WHITE = (255, 255, 255)
AMBER = (245, 166, 35)

ROOT = (128, 70)
CHILDREN = [(62, 160), (128, 160), (194, 160)]
GRANDCHILD = (194, 222)
NODE_R, EDGE_R, CORNER = 21, 7, 52


def in_tile(x: float, y: float) -> bool:
    cx = min(max(x, CORNER), SIZE - CORNER)
    cy = min(max(y, CORNER), SIZE - CORNER)
    return (x - cx) ** 2 + (y - cy) ** 2 <= CORNER ** 2


def near_segment(x, y, a, b, r) -> bool:
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - ax - t * dx, y - ay - t * dy) <= r


def color_at(x: float, y: float):
    """RGBA of one sample point."""
    if not in_tile(x, y):
        return (0, 0, 0, 0)
    for node, color in ([(c, WHITE) for c in [ROOT] + CHILDREN[:2]]
                        + [(CHILDREN[2], AMBER), (GRANDCHILD, AMBER)]):
        if math.hypot(x - node[0], y - node[1]) <= NODE_R:
            return color + (255,)
    edges = [(ROOT, c) for c in CHILDREN]
    if any(near_segment(x, y, a, b, EDGE_R) for a, b in edges):
        return WHITE + (255,)
    if near_segment(x, y, CHILDREN[2], GRANDCHILD, EDGE_R):
        return AMBER + (255,)
    k = y / SIZE
    return tuple(round(t + (b - t) * k) for t, b in zip(TILE_TOP, TILE_BOTTOM)) + (255,)


def pixel(px: int, py: int):
    total = [0, 0, 0, 0]
    for sy in range(SAMPLES):
        for sx in range(SAMPLES):
            r, g, b, a = color_at(px + (sx + 0.5) / SAMPLES, py + (sy + 0.5) / SAMPLES)
            total[0] += r * a
            total[1] += g * a
            total[2] += b * a
            total[3] += a
    alpha = total[3]
    if not alpha:
        return (0, 0, 0, 0)
    return (round(total[0] / alpha), round(total[1] / alpha), round(total[2] / alpha),
            round(alpha / SAMPLES ** 2))


def png(rows) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    raw = b"".join(b"\0" + bytes(v for p in row for v in p) for row in rows)
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    with open("icon.png", "wb") as f:
        f.write(png([[pixel(x, y) for x in range(SIZE)] for y in range(SIZE)]))

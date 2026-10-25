"""纯标准库 PNG 解码与终端二维码渲染（零第三方依赖）。"""
from __future__ import annotations

import struct
import zlib


def _unfilter(f: int, line: bytearray, prev: bytes, bpp: int) -> bytes:
    """PNG 反滤波（None/Sub/Up/Average/Paeth）。"""
    stride = len(line)
    for x in range(stride):
        a = line[x - bpp] if x >= bpp else 0
        b = prev[x]
        c = prev[x - bpp] if x >= bpp else 0
        if f == 1:
            line[x] = (line[x] + a) & 0xFF
        elif f == 2:
            line[x] = (line[x] + b) & 0xFF
        elif f == 3:
            line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
        elif f == 4:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line[x] = (line[x] + pr) & 0xFF
    return bytes(line)


def _read_gray(row: bytes, x: int, color_type: int, bit_depth: int, channels: int, palette: list[tuple[int, int, int]]) -> int:
    """读取 (x, y) 像素并转为灰度 0-255。"""
    if bit_depth == 8:
        o = x * channels
        if color_type in (0, 4):
            return row[o]
        if color_type in (2, 6):
            r, g, b = row[o], row[o + 1], row[o + 2]
            return (r * 299 + g * 587 + b * 114) // 1000
        idx = row[o]  # 调色板
        if idx < len(palette):
            r, g, b = palette[idx]
            return (r * 299 + g * 587 + b * 114) // 1000
        return 255
    # sub-byte 灰度（1/2/4 位），如腾讯二维码的 1 位深 PNG
    per_byte = 8 // bit_depth
    value = (row[x // per_byte] >> ((per_byte - 1 - (x % per_byte)) * bit_depth)) & ((1 << bit_depth) - 1)
    return value * 255 // ((1 << bit_depth) - 1)


def decode_png(data: bytes) -> tuple[int, int, list[list[int]]]:
    """
    解码 PNG，返回 (宽, 高, 灰度矩阵 0-255)。

    支持灰度(0)/RGB(2)/调色板(3)/灰度+Alpha(4)/RGBA(6)，位深 1/2/4/8（非交错）。
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 图片")
    pos = 8
    idat = b""
    w = h = bit_depth = color_type = interlace = 0
    palette: list[tuple[int, int, int]] = []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"PLTE":
            palette = [
                (chunk[i], chunk[i + 1], chunk[i + 2])
                for i in range(0, len(chunk), 3)
            ]
        elif ctype == b"IEND":
            break
    if interlace != 0:
        raise ValueError("暂不支持交错 PNG")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bpp = 1 if bit_depth < 8 else channels  # 反滤波的字节步长
    raw = zlib.decompress(idat)
    stride = (w * bpp + 7) // 8 if bit_depth < 8 else w * bpp
    rows: list[bytes] = []
    prev = b"\x00" * stride
    p = 0
    for _ in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        rows.append(_unfilter(f, line, prev, bpp))
        prev = rows[-1]

    return w, h, [
        [_read_gray(row, x, color_type, bit_depth, channels, palette) for x in range(w)]
        for row in rows
    ]


def render_qr_ascii(image: bytes, max_width: int = 76) -> str:
    """
    把二维码图片渲染为终端半块字符画，可直接打印在控制台。

    使用 █ / ▀ / ▄ / 空格 组合，每字符显示 1x2 像素块，视觉上接近正方形。
    """
    w, h, gray = decode_png(image)
    scale = max(1, min(w, h) // max_width)
    cols, rows = w // scale, h // scale

    cells: list[list[bool]] = []
    for y in range(rows):
        row = []
        for x in range(cols):
            total = 0
            for dy in range(scale):
                for dx in range(scale):
                    total += gray[y * scale + dy][x * scale + dx]
            row.append(total / (scale * scale) < 140)  # 暗像素
        cells.append(row)

    lines = []
    for y in range(0, rows, 2):
        line = ""
        for x in range(cols):
            top = cells[y][x]
            bottom = cells[y + 1][x] if y + 1 < rows else False
            if top and bottom:
                line += "█"
            elif top:
                line += "▀"
            elif bottom:
                line += "▄"
            else:
                line += " "
        lines.append(line)
    return "\n".join(lines)

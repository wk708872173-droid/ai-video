"""测试公共工具：最小 PNG 生成（无 Pillow 依赖）。

设计：
  - _make_min_png：生成固定 RGB 的 16x16 PNG（用于 PIL 读图验证）
  - _make_noise_png：生成确定性噪声 PNG（seed 不同 → hash 不同）

提取自：
  - tests/test_editing.py（V3.2 REQ-303 测试）
  - tests/test_face_consistency.py（V3.2 REQ-402 测试）

用法：
    from tests._png_utils import make_min_png, make_noise_png
"""
from __future__ import annotations

import random
import struct
import zlib
from pathlib import Path


def make_min_png(
    path: Path,
    *,
    width: int = 16,
    height: int = 16,
    rgb: tuple[int, int, int] = (128, 128, 128),
) -> None:
    """生成固定 RGB 的最小 PNG（无 Pillow 依赖）。"""
    r, g, b = rgb

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + (struct.pack(">BBB", r, g, b) * width)
    raw = row * height
    idat = zlib.compress(raw, 9)
    path.write_bytes(
        sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )


def make_noise_png(path: Path, *, seed: int = 1, width: int = 16, height: int = 16) -> None:
    """生成确定性"噪声" PNG（伪随机亮度块）——seed 不同 → hash 不同。

    用于测试两张明显不同图片的 similarity < 1.0（避免单色图像 hash 退化）。
    """
    rng = random.Random(seed)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = []
    for _ in range(height):
        pixels = bytes(rng.randint(0, 255) for _ in range(width * 3))
        rows.append(b"\x00" + pixels)
    raw = b"".join(rows)
    idat = zlib.compress(raw, 9)
    path.write_bytes(
        sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )


def make_pattern_png(path: Path, *, base: int = 0, delta: int = 50) -> None:
    """生成有梯度的 PNG（每行 +delta 像素值）——用于测试 hash 重映射。

    注：单色或简单渐变会让 perceptual hash 退化（hash=0），用 noise 更稳健。
    """
    width, height = 16, 16

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = []
    for r_idx in range(height):
        v = min(255, base + delta * r_idx)
        rows.append(b"\x00" + (struct.pack(">BBB", v, v, v) * width))
    raw = b"".join(rows)
    idat = zlib.compress(raw, 9)
    path.write_bytes(
        sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )

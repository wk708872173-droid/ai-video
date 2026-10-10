"""V3.2 重构：quality 子模块拆分 — 面部相似度比对（REQ-402）。

原 quality.py:
  - L484-505 (FaceConsistencyReport)
  - L508-515 (FaceSimilarityBackend ABC)
  - L520-575 (HashFaceSimilarityBackend)
  - L580-591 (NullFaceSimilarityBackend)
  - L594-611 (_DEFAULT_BACKEND / get_default_backend / set_default_backend)
  - L614-658 (compute_face_consistency)
  - L662-684 (RealFaceRecognitionBackend)
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------- FaceConsistencyReport ----------

@dataclass
class FaceConsistencyReport:
    """面部一致性报告。"""
    mean_similarity: float = 0.0       # 所有相邻对平均相似度 [0,1]
    min_similarity: float = 0.0        # 最低相似度
    flagged_pairs: list[tuple[int, int, float]] = field(default_factory=list)  # (i, j, sim)
    warnings: list[str] = field(default_factory=list)
    backend_name: str = ""

    def to_dict(self) -> dict:
        return {
            "mean_similarity": self.mean_similarity,
            "min_similarity": self.min_similarity,
            "flagged_pairs": [list(p) for p in self.flagged_pairs],
            "warnings": list(self.warnings),
            "backend_name": self.backend_name,
        }

    @property
    def ok(self) -> bool:
        """无 flagged pair 即视为通过。"""
        return len(self.flagged_pairs) == 0


class FaceSimilarityBackend(abc.ABC):
    """面部相似度后端 ABC。"""

    name: str = ""

    @abc.abstractmethod
    def similarity(self, img1_path: str | Path, img2_path: str | Path) -> float:
        """两张图片的相似度 [0,1]。1.0 = 完全一致。"""


# ---------- 轻量实现：aHash + dHash（perceptual hash） ----------

class HashFaceSimilarityBackend(FaceSimilarityBackend):
    """轻量面部相似度后端：PIL aHash（8x8 平均）+ dHash（梯度）。

    优势：零额外依赖（PIL 已有），跑得快（每对 < 50ms）。
    局限：不检测"脸"——比较的是整图；构图相似但脸不同的图也可能高相似。

    用法：
        backend = HashFaceSimilarityBackend()
        sim = backend.similarity("shot_001.jpg", "shot_002.jpg")
    """

    name: str = "hash"

    HASH_SIZE: int = 8

    def _load_gray(self, path: str | Path) -> list[int]:
        """PIL 读图 → 灰度 → 缩到 HASH_SIZE x HASH_SIZE。"""
        from PIL import Image
        img = Image.open(str(path)).convert("L").resize(
            (self.HASH_SIZE, self.HASH_SIZE),
            Image.Resampling.LANCZOS,
        )
        return list(img.getdata())

    def _ahash(self, pixels: list[int]) -> int:
        """平均哈希（average hash）：像素 vs 均值 → 8x8 = 64 bits。"""
        avg = sum(pixels) / len(pixels)
        bits = 0
        for i, p in enumerate(pixels):
            if p > avg:
                bits |= 1 << i
        return bits

    def _dhash(self, pixels: list[int]) -> int:
        """差分哈希（difference hash）：每行后像素 vs 前像素 → 8x8 = 64 bits。"""
        bits = 0
        # pixels 是 HASH_SIZE x HASH_SIZE = 64 个；每行 8 个 → 8 行 × 8 差
        for row in range(self.HASH_SIZE):
            for col in range(self.HASH_SIZE - 1):
                idx = row * self.HASH_SIZE + col
                if pixels[idx] > pixels[idx + 1]:
                    bits |= 1 << (row * (self.HASH_SIZE - 1) + col)
        return bits

    def _hamming(self, h1: int, h2: int) -> int:
        return bin(h1 ^ h2).count("1")

    def similarity(self, img1_path: str | Path, img2_path: str | Path) -> float:
        p1 = self._load_gray(img1_path)
        p2 = self._load_gray(img2_path)
        a1, a2 = self._ahash(p1), self._ahash(p2)
        d1, d2 = self._dhash(p1), self._dhash(p2)
        # 两个 hash 各 64 bits；合并 hamming
        ham = self._hamming(a1, a2) + self._hamming(d1, d2)
        max_bits = 64 * 2
        return 1.0 - ham / max_bits


# ---------- Null 后端（永远通过，用于测试） ----------

class NullFaceSimilarityBackend(FaceSimilarityBackend):
    """始终返 1.0（完全一致）的 Null 后端。

    用于：
      - 单测 QA 链路不依赖具体相似度算法
      - pipeline 关闭面部检查（默认 fallback）
    """

    name: str = "null"

    def similarity(self, img1_path: str | Path, img2_path: str | Path) -> float:
        return 1.0


# ---------- 公开 API：compute_face_consistency ----------

_DEFAULT_THRESHOLD = 0.85
_DEFAULT_BACKEND: FaceSimilarityBackend | None = None


def get_default_backend() -> FaceSimilarityBackend:
    """懒加载默认 backend（HashFaceSimilarityBackend）。"""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = HashFaceSimilarityBackend()
    return _DEFAULT_BACKEND


def set_default_backend(backend: FaceSimilarityBackend | None) -> None:
    """注入默认 backend（单测）。None = 还原 hash。"""
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = backend if backend is not None else None


def compute_face_consistency(
    image_paths: list[str | Path],
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    backend: FaceSimilarityBackend | None = None,
) -> FaceConsistencyReport:
    """逐对算面部相似度，返回报告。

    Args:
        image_paths: N 张图（同一角色的不同 shot）
        threshold: 相似度低于此值的 pair 计入 flagged_pairs
        backend: 自定义 backend；None = 用 HashFaceSimilarityBackend

    Returns:
        FaceConsistencyReport（mean_similarity / min_similarity / flagged_pairs）
    """
    rep = FaceConsistencyReport()
    be = backend or get_default_backend()
    rep.backend_name = be.name

    paths = [Path(p) for p in image_paths]
    if len(paths) < 2:
        rep.warnings.append("需要 ≥ 2 张图才能算面部一致性")
        return rep
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            rep.warnings.append(f"图片不存在：{p}")
        return rep

    sims: list[float] = []
    for i in range(len(paths) - 1):
        try:
            sim = be.similarity(paths[i], paths[i + 1])
        except Exception as exc:  # PIL 解析失败等
            rep.warnings.append(f"shot {i+1} ↔ {i+2}: {type(exc).__name__}: {exc}")
            continue
        sims.append(sim)
        if sim < threshold:
            rep.flagged_pairs.append((i, i + 1, sim))

    if sims:
        rep.mean_similarity = sum(sims) / len(sims)
        rep.min_similarity = min(sims)
    return rep


# REQ-402 验收用：仅占位 / stub；真脸识别待 pip install face_recognition 后接入
class RealFaceRecognitionBackend(FaceSimilarityBackend):
    """face_recognition 真后端（stub）。

    实装：pip install face_recognition + dlib。
    思路：face_recognition.face_encodings() → 128-d vector → face_distance()。

    当前 raise NotImplementedError——告诉用户装库的明确指引。
    """

    name: str = "face_recognition"

    def similarity(self, img1_path: str | Path, img2_path: str | Path) -> float:
        raise NotImplementedError(
            "RealFaceRecognitionBackend 实装需 `pip install face_recognition`：\n"
            "  1. pip install face_recognition\n"
            "  2. 替换 similarity() 内部为：\n"
            "     import face_recognition\n"
            "     enc1 = face_recognition.face_encodings(face_recognition.load_image_file(str(img1_path)))[0]\n"
            "     enc2 = face_recognition.face_encodings(face_recognition.load_image_file(str(img2_path)))[0]\n"
            "     dist = face_recognition.face_distance([enc1], enc2)[0]\n"
            "     return max(0.0, 1.0 - dist)\n"
            "目前先用 HashFaceSimilarityBackend（零依赖代理）。"
        )

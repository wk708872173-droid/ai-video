"""V3.8 W6 — VLM 评审闭环的**接口层**（默认不接真 API）。

## 定位

这是 REQ-407「反馈闭环（播放量反哺提示词）」与「提示词自动优化」的**数据/接口
前置**。本轮**只建接口**：评审器抽象、评分维度、报告结构、CLI 子命令、
mock 后端 —— **默认关闭、零网络调用、零计费**。

真 API 后端（`GeminiVLMReviewer`）已实现但**必须显式开启**（`--enable-real`
+ `GEMINI_VLM_MODEL`），默认走 `MockVLMReviewer`。

## 文献依据

- **arXiv:2603.12310「VQQA」**：多智能体框架，**动态生成视觉问题**、用 VLM critique
  当语义梯度，取代被动指标，走**黑盒自然语言接口**做闭环提示词优化。
  → 本模块是那个"黑盒自然语言接口"的落地：评审器输入图 + 上下文，输出
  **可解释的多维评分**。
- **arXiv:2509.22799「VideoScore2」**：多维、可解释、人类对齐；评 visual quality /
  text-to-video alignment / physical consistency，并输出**chain-of-thought 理由**。
  → `ReviewReport` 的 `scores` + `rationales` 双结构。
- **arXiv:2604.28078「AesRM」**：层级 rubric —— Visual Aesthetics / Visual Fidelity /
  Visual Plausibility，15 项细粒度准则（如 shot composition）。
  → 维度描述要落到"可判定的画面特征"，不是"好看"这种主观词。

## 红线

- 默认 `MockVLMReviewer` → **不发起任何网络请求**。
- 真后端需要 `GEMINI_API_KEY` + `GEMINI_VLM_MODEL` + `--enable-real` 三者齐备，
  缺一即**显式报错**（鉴权域硬失败，不静默降级到 mock —— 那会让用户以为评审过了）。
- 评审**只报告不阻断**（与项目既有 SSIM / 连续性门禁一致）。
"""
from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 评审维度（5 维，对齐 LoCoT2V-Eval 五轴 + VideoScore2 的可解释输出 + AesRM 的可判定准则）
REVIEW_DIMENSIONS: dict[str, str] = {
    "perceptual_quality": (
        "感知质量：清晰度、压缩伪影、手部/肢体畸变、构图是否失衡、"
        "有没有文字/水印污染"
    ),
    "text_video_alignment": (
        "文-画对齐：画面是否兑现了分镜描述里的角色外观、场景、动作、氛围"
    ),
    "temporal_consistency": (
        "时序一致性：角色长相/颜色是否跨帧漂移，场景与光线是否跳变，"
        "有没有闪烁或鬼影"
    ),
    "dynamic_quality": (
        "动态质量：运动是否自然连贯，有没有形变、拖影、多余主体或静止假动态"
    ),
    "narrative_expectation": (
        "叙事达成度：情绪与节拍是否落地，画面是否传达了旁白/对白想说的意思"
    ),
}

# 通过线（低于则 flag；**只报警不阻断**）
DEFAULT_REVIEW_THRESHOLD = 0.6

# 默认后端
DEFAULT_VLM_MODEL = "gemini-2.5-flash"


class VLMReviewError(RuntimeError):
    """评审器配置 / 调用错误（鉴权域：硬失败，不静默降级）。"""


@dataclass
class ReviewReport:
    """一次评审的结果。

    双结构（VideoScore2 式）：`scores` 给分 + `rationales` 给理由。
    """
    scores: dict[str, float] = field(default_factory=dict)
    rationales: dict[str, str] = field(default_factory=dict)
    overall: float = 0.0
    source: str = "mock"
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    context: str = ""

    @property
    def flagged_dimensions(self) -> list[str]:
        return [d for d, s in self.scores.items() if s < DEFAULT_REVIEW_THRESHOLD]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": dict(self.scores),
            "rationales": dict(self.rationales),
            "overall": self.overall,
            "source": self.source,
            "ok": self.ok,
            "issues": list(self.issues),
            "context": self.context,
            "flagged_dimensions": self.flagged_dimensions,
        }

    def format(self) -> str:
        lines = [f"VLM 评审（source={self.source}，overall={self.overall:.3f}）："]
        for dim, desc in REVIEW_DIMENSIONS.items():
            score = self.scores.get(dim)
            if score is None:
                continue
            why = self.rationales.get(dim, "")
            lines.append(f"  - {dim}（{desc[:24]}…）: {score:.3f}" + (f" — {why}" if why else ""))
        if self.flagged_dimensions:
            lines.append(f"[WARN] 未达标维度：{', '.join(self.flagged_dimensions)}")
        for issue in self.issues:
            lines.append(f"[WARN] {issue}")
        if not self.issues and not self.flagged_dimensions:
            lines.append("[OK] 各维度均达标")
        return "\n".join(lines)


# ---------- 评审器 ----------

class VLMReviewer(ABC):
    """评审器抽象。"""
    name = "vlm"

    @abstractmethod
    def review(self, image_paths: list[str | Path], *, context: str = "") -> ReviewReport:
        """评审一组图（按时间顺序），返回可解释的多维评分。"""


class MockVLMReviewer(VLMReviewer):
    """Mock 评审器：**零网络调用**，确定性评分。

    用途：把接口 / 维度 / 报告结构 / CLI 链路先跑扎实（不烧真 API 额度）。
    评分是**固定值**（不是从图像算的）—— 目的是让下游契约稳定可测。
    """
    name = "mock"

    _SCORES: dict[str, float] = {
        "perceptual_quality": 0.85,
        "text_video_alignment": 0.80,
        "temporal_consistency": 0.78,
        "dynamic_quality": 0.75,
        "narrative_expectation": 0.82,
    }

    _RATIONALES: dict[str, str] = {
        "perceptual_quality": "mock：未发现明显伪影或畸变",
        "text_video_alignment": "mock：角色/场景与描述基本吻合",
        "temporal_consistency": "mock：跨帧外观稳定（需真模型确认）",
        "dynamic_quality": "mock：运动连贯（需真模型确认）",
        "narrative_expectation": "mock：情绪表达中性（需真模型确认）",
    }

    def review(self, image_paths: list[str | Path], *, context: str = "") -> ReviewReport:
        scores = dict(self._SCORES)
        rationales = dict(self._RATIONALES)
        overall = sum(scores.values()) / len(scores) if scores else 0.0
        report = ReviewReport(
            scores=scores, rationales=rationales, overall=overall,
            source=self.name, context=context,
        )
        if not image_paths:
            report.ok = False
            report.issues.append("没有待评审的图片")
        return report


class GeminiVLMReviewer(VLMReviewer):
    """Gemini VLM 评审器（真 API）。

    **默认不启用**：必须同时满足 ① `--enable-real` ② `GEMINI_API_KEY`
    ③ `GEMINI_VLM_MODEL`。缺任一项 → 显式报错（鉴权域硬失败）。

    `http_post` 是注入点（与 image_engine 一致）→ 单测不需要网络。
    """
    name = "gemini"

    def __init__(
        self,
        *,
        api_key_value: str | None = None,
        base_url_value: str | None = None,
        model_value: str | None = None,
        http_post: Callable[[str, dict, dict], dict] | None = None,
        timeout: int = 120,
    ) -> None:
        key = api_key_value if api_key_value is not None else os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise VLMReviewError("GEMINI_API_KEY 未设置；评审器需要凭据")
        base = base_url_value or os.environ.get(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        )
        model = model_value or os.environ.get("GEMINI_VLM_MODEL", "")
        if not model:
            raise VLMReviewError(
                "GEMINI_VLM_MODEL 未设置；请指定评审用的多模态模型"
                f"（如 {DEFAULT_VLM_MODEL}）"
            )
        self._api_key = key
        self._base_url = base.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._http_post = http_post

    def review(self, image_paths: list[str | Path], *, context: str = "") -> ReviewReport:
        if not image_paths:
            report = ReviewReport(source=self.name, context=context)
            report.ok = False
            report.issues.append("没有待评审的图片")
            return report

        parts: list[dict[str, Any]] = []
        for p in image_paths:
            path = Path(p)
            if not path.is_file():
                continue
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append({"inlineData": {"mimeType": _guess_mime(path), "data": data}})
        parts.append({"text": build_review_prompt(image_paths, context=context)})

        url = f"{self._base_url}/models/{self._model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}
        body = {"contents": [{"role": "user", "parts": parts}]}

        try:
            if self._http_post is not None:
                data = self._http_post(url, body, headers)
            else:
                import urllib.request
                req = urllib.request.Request(
                    url, data=json.dumps(body).encode("utf-8"),
                    headers=headers, method="POST",
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise VLMReviewError(f"Gemini 评审调用失败：{type(exc).__name__}: {exc}") from exc

        raw = _extract_text(data)
        report = parse_review_response(raw)
        report.source = self.name
        report.context = context
        return report


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(suffix, "image/jpeg")


def _extract_text(data: Any) -> str:
    """从 Gemini 响应里取第一个文本 part。"""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        for part in parts:
            if "text" in part:
                return str(part["text"])
    except (KeyError, IndexError, TypeError):
        pass
    return ""


# ---------- prompt / 解析 ----------

def build_review_prompt(image_paths: list[str | Path], *, context: str = "") -> str:
    """构造评审 prompt（维度清单 + 判定标准 + 输出格式）。"""
    lines = [
        "你是 AI 漫剧成片的画面质量评审。请按下列维度给 0..1 的分数，并为每个维度"
        "写一句**可判定**的理由（说明看到了什么，不要写'好看'这类主观词）。",
        "",
        "维度：",
    ]
    for dim, desc in REVIEW_DIMENSIONS.items():
        lines.append(f"- {dim}: {desc}")
    lines += [
        "",
        "输出严格 JSON（不要任何额外文字）：",
        '{"scores": {"<维度>": 0.0}, "rationales": {"<维度>": "理由"}}',
    ]
    if context:
        lines += ["", "本次评审上下文（分镜描述 / 旁白 / 角色设定）：", context]
    lines += ["", f"共 {len(list(image_paths))} 张图，按时间顺序。"]
    return "\n".join(lines)


def parse_review_response(raw: str) -> ReviewReport:
    """解析评审响应。

    **容错**：非 JSON / 结构不符 → 返回**空报告**（不抛错）。
    评审是质量信号，不是故障点。
    """
    report = ReviewReport(source="parsed")
    text = (raw or "").strip()
    if not text:
        report.ok = False
        report.issues.append("评审响应为空")
        return report
    # 去掉 fenced code block
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        data = json.loads(text)
    except ValueError:
        report.ok = False
        report.issues.append("评审响应不是合法 JSON（已忽略）")
        return report
    if not isinstance(data, dict):
        report.ok = False
        report.issues.append("评审响应结构不是对象（已忽略）")
        return report

    raw_scores = data.get("scores") or {}
    if isinstance(raw_scores, dict):
        for dim, value in raw_scores.items():
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            report.scores[str(dim)] = max(0.0, min(1.0, score))
    raw_rationales = data.get("rationales") or {}
    if isinstance(raw_rationales, dict):
        for dim, value in raw_rationales.items():
            report.rationales[str(dim)] = str(value)

    if report.scores:
        report.overall = sum(report.scores.values()) / len(report.scores)
        report.ok = not report.flagged_dimensions
        if not report.ok:
            report.issues.append(
                f"未达标维度：{', '.join(report.flagged_dimensions)}"
            )
    else:
        report.ok = False
        report.issues.append("评审响应里没有可解析的分数")
    return report


# ---------- 工厂 ----------

def make_reviewer(prefer: str = "mock", *, enable_real: bool = False) -> VLMReviewer:
    """建评审器。

    Args:
        prefer: "mock" | "gemini"
        enable_real: 是否允许真 API。False 时即使 prefer="gemini" 也**显式报错**
            （鉴权域硬失败 —— 静默退回 mock 会让用户误以为评审过了）。

    Raises:
        VLMReviewError: prefer 未知、或 prefer="gemini" 但未 enable_real
    """
    if prefer == "mock":
        return MockVLMReviewer()
    if prefer == "gemini":
        if not enable_real:
            raise VLMReviewError(
                "真 API 评审需显式开启：加 --enable-real"
                "（默认关闭，避免意外计费）"
            )
        return GeminiVLMReviewer()
    raise VLMReviewError(f"未知 reviewer backend: {prefer}")


__all__ = [
    "DEFAULT_REVIEW_THRESHOLD",
    "DEFAULT_VLM_MODEL",
    "REVIEW_DIMENSIONS",
    "GeminiVLMReviewer",
    "MockVLMReviewer",
    "ReviewReport",
    "VLMReviewError",
    "VLMReviewer",
    "build_review_prompt",
    "make_reviewer",
    "parse_review_response",
]

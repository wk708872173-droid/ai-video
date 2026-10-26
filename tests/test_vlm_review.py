"""V3.8 Batch 6 — W6 VLM 评审闭环接口（mock 优先，**不接真 API**）。

REQ-407「反馈闭环」与「提示词自动优化」的前置：评审器接口、评分维度、报告结构、
CLI 子命令。

文献依据：arXiv:2603.12310（VQQA：VLM critique 当语义梯度，黑盒 NL 接口）、
arXiv:2509.22799（VideoScore2：多维可解释 + CoT 理由）、
arXiv:2604.28078（AesRM：可判定的层级 rubric）。

红线：
  - 默认 mock → **零网络调用**；
  - 真 API 需 `--enable-real` + `GEMINI_API_KEY` + `GEMINI_VLM_MODEL`，
    缺一即**显式报错**（鉴权域硬失败，**不静默退回 mock**）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import vlm_review as vr  # noqa: E402


# ---------- helpers ----------

def _write_img(path: Path, color=(200, 180, 140)) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)
    return path


# ============================================================
# 维度与报告结构
# ============================================================

def test_review_dimensions_cover_five_axes():
    """5 维，对齐 LoCoT2V-Eval 的五轴。"""
    for dim in ("perceptual_quality", "text_video_alignment",
                "temporal_consistency", "dynamic_quality",
                "narrative_expectation"):
        assert dim in vr.REVIEW_DIMENSIONS, f"缺少评审维度 {dim}"
    assert len(vr.REVIEW_DIMENSIONS) == 5


def test_dimension_descriptions_are_actionable():
    """维度描述要落到可判定的画面特征（AesRM 式），不是"好看"。"""
    for dim, desc in vr.REVIEW_DIMENSIONS.items():
        assert len(desc) > 10, f"{dim} 描述太短"


def test_report_to_dict_is_stable():
    rep = vr.MockVLMReviewer().review([])
    d = rep.to_dict()
    assert set(d) >= {"scores", "rationales", "overall", "source", "ok", "issues"}
    json.dumps(d, ensure_ascii=False)


def test_report_format_lists_dimensions(tmp_path):
    img = _write_img(tmp_path / "a.jpg")
    rep = vr.MockVLMReviewer().review([img])
    text = rep.format()
    assert "perceptual_quality" in text
    assert "overall" in text


# ============================================================
# Mock 评审器
# ============================================================

def test_mock_reviewer_scores_all_dimensions(tmp_path):
    img = _write_img(tmp_path / "a.jpg")
    rep = vr.MockVLMReviewer().review([img])
    assert set(rep.scores) == set(vr.REVIEW_DIMENSIONS)
    assert all(0.0 <= s <= 1.0 for s in rep.scores.values())
    assert 0.0 <= rep.overall <= 1.0


def test_mock_reviewer_gives_rationale_per_dimension(tmp_path):
    img = _write_img(tmp_path / "a.jpg")
    rep = vr.MockVLMReviewer().review([img])
    for dim in vr.REVIEW_DIMENSIONS:
        assert rep.rationales.get(dim), f"{dim} 缺少理由"


def test_mock_reviewer_with_no_images_is_not_ok():
    rep = vr.MockVLMReviewer().review([])
    assert rep.ok is False
    assert rep.issues


def test_mock_reviewer_makes_no_network_call(tmp_path, monkeypatch):
    """默认路径**零网络调用**（红线）。"""
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("mock 评审器不得发起网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    img = _write_img(tmp_path / "a.jpg")
    rep = vr.MockVLMReviewer().review([img])
    assert rep.ok is True


# ============================================================
# 工厂与默认关闭
# ============================================================

def test_make_reviewer_mock_by_default():
    assert isinstance(vr.make_reviewer("mock"), vr.MockVLMReviewer)


def test_make_reviewer_gemini_requires_enable_real():
    """真 API 必须显式开启 —— 静默退回 mock 会让用户误以为评审过了。"""
    with pytest.raises(vr.VLMReviewError, match="enable-real"):
        vr.make_reviewer("gemini", enable_real=False)


def test_make_reviewer_unknown_backend_raises():
    with pytest.raises(vr.VLMReviewError):
        vr.make_reviewer("nope")


def test_gemini_reviewer_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_VLM_MODEL", "gemini-2.5-flash")
    with pytest.raises(vr.VLMReviewError, match="GEMINI_API_KEY"):
        vr.GeminiVLMReviewer()


def test_gemini_reviewer_requires_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.delenv("GEMINI_VLM_MODEL", raising=False)
    with pytest.raises(vr.VLMReviewError, match="GEMINI_VLM_MODEL"):
        vr.GeminiVLMReviewer()


def test_gemini_reviewer_parses_injected_response(tmp_path):
    """http_post 是注入点 → 单测不需要网络。"""
    captured: dict = {}

    def fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({
            "scores": {"perceptual_quality": 0.9, "text_video_alignment": 0.4},
            "rationales": {"perceptual_quality": "清晰"},
        })}]}}]}

    reviewer = vr.GeminiVLMReviewer(
        api_key_value="AIza-test",
        model_value="gemini-2.5-flash",
        http_post=fake_post,
    )
    img = _write_img(tmp_path / "a.jpg")
    rep = reviewer.review([img], context="ctx")
    assert rep.scores["perceptual_quality"] == 0.9
    assert rep.scores["text_video_alignment"] == 0.4
    assert rep.source == "gemini"
    assert rep.ok is False          # 0.4 < 0.6 → flag
    assert "text_video_alignment" in rep.flagged_dimensions
    assert captured["url"].endswith(":generateContent")


# ============================================================
# prompt 与解析
# ============================================================

def test_build_review_prompt_lists_dimensions():
    text = vr.build_review_prompt(["a.jpg", "b.jpg"], context="一只狗在田野")
    for dim in vr.REVIEW_DIMENSIONS:
        assert dim in text
    assert "一只狗在田野" in text
    assert "JSON" in text


def test_parse_review_response_valid():
    raw = json.dumps({
        "scores": {"perceptual_quality": 0.9, "dynamic_quality": 0.7},
        "rationales": {"perceptual_quality": "无伪影"},
    })
    rep = vr.parse_review_response(raw)
    assert rep.scores["perceptual_quality"] == 0.9
    assert rep.rationales["perceptual_quality"] == "无伪影"
    assert rep.ok is True


def test_parse_review_response_handles_fenced_block():
    raw = "```json\n" + json.dumps({"scores": {"perceptual_quality": 0.8}}) + "\n```"
    rep = vr.parse_review_response(raw)
    assert rep.scores["perceptual_quality"] == 0.8


def test_parse_review_response_garbage_returns_empty_report():
    """非 JSON → 空报告，**不抛错**（评审是质量信号，不是故障点）。"""
    rep = vr.parse_review_response("garbage output")
    assert rep.scores == {}
    assert rep.ok is False
    assert rep.issues


def test_parse_review_response_empty_string():
    rep = vr.parse_review_response("")
    assert rep.ok is False


def test_parse_review_response_clamps_scores():
    raw = json.dumps({"scores": {"perceptual_quality": 1.8, "dynamic_quality": -0.2}})
    rep = vr.parse_review_response(raw)
    assert rep.scores["perceptual_quality"] == 1.0
    assert rep.scores["dynamic_quality"] == 0.0


def test_parse_review_response_ignores_bad_score_types():
    raw = json.dumps({"scores": {"perceptual_quality": "很好"}})
    rep = vr.parse_review_response(raw)
    assert "perceptual_quality" not in rep.scores
    assert rep.ok is False


def test_parse_review_response_non_object():
    rep = vr.parse_review_response("[1, 2, 3]")
    assert rep.ok is False


# ============================================================
# CLI
# ============================================================

def test_cli_review_subcommand_exists():
    from hailuo_video import cli

    parser = cli.build_parser()
    args = parser.parse_args(["review", "a.jpg", "b.jpg"])
    assert args.images == ["a.jpg", "b.jpg"]
    assert args.backend == "mock"
    assert args.enable_real is False


def test_cli_review_default_is_mock_and_makes_no_call(tmp_path, capsys):
    from hailuo_video import cli

    img = _write_img(tmp_path / "a.jpg")
    code = cli.main(["review", str(img)])
    assert code == 0
    err = capsys.readouterr().err
    assert "perceptual_quality" in err


def test_cli_review_writes_report(tmp_path, capsys):
    from hailuo_video import cli

    img = _write_img(tmp_path / "a.jpg")
    out = tmp_path / "report.json"
    code = cli.main(["review", str(img), "--output", str(out)])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source"] == "mock"
    assert data["scores"]


def test_cli_review_rejects_missing_images(tmp_path, capsys):
    from hailuo_video import cli

    code = cli.main(["review", str(tmp_path / "nope.jpg")])
    assert code == 2
    assert "[FAIL]" in capsys.readouterr().err


def test_cli_review_real_backend_requires_enable_real(tmp_path, capsys):
    from hailuo_video import cli

    img = _write_img(tmp_path / "a.jpg")
    code = cli.main(["review", str(img), "--backend", "gemini"])
    assert code == 2
    assert "enable-real" in capsys.readouterr().err


def test_cli_review_requires_at_least_one_image():
    from hailuo_video import cli

    code = cli.main(["review"])
    assert code == 2

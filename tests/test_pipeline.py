"""REQ-V2.5 pipeline 单测。注入 mock backend，端到端跑通。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import (  # noqa: E402
    concat_engine as ce,
    image_engine as ie,
    pipeline as pl,
    script_engine as se,
    tts_engine as te,
    video_engine as ve,
)
from hailuo_video.concat_engine import set_runner  # noqa: E402


# ---------- 测试用真实 storyboard（手写，不走 LLM） ----------

def _make_storyboard(tmp_path, *, title: str = "t", n_shots: int = 2) -> se.Storyboard:
    shots = []
    for i in range(n_shots):
        shots.append(se.Shot(
            index=i + 1, scene_id=1,
            shot_type="中景",
            description=f"镜头 {i+1} 描述",
            characters=[],
            dialogue="",
            voiceover=f"镜头{i+1}旁白" if i % 2 == 0 else "",
            mood=["平和"],
            camera_motion="推近",
        ))
    return se.Storyboard(title=title, characters=[], shots=shots)


def _make_storyboard_json(tmp_path, n_shots: int = 2) -> Path:
    """写一个 storyboard.json 文件供 pipeline.load_storyboard 读。"""
    sb = _make_storyboard(tmp_path, n_shots=n_shots)
    data = {
        "title": sb.title,
        "characters": [],
        "shots": [
            {
                "index": s.index, "scene_id": s.scene_id, "shot_type": s.shot_type,
                "description": s.description, "characters": s.characters,
                "dialogue": s.dialogue, "voiceover": s.voiceover,
                "mood": s.mood, "camera_motion": s.camera_motion,
            }
            for s in sb.shots
        ],
    }
    p = tmp_path / "sb.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------- ffmpeg runner mock ----------

@pytest.fixture
def fake_ffmpeg_runner(monkeypatch):
    """所有 ffmpeg 调用返回成功并写假输出。"""
    def runner(args, timeout):
        output = Path(args[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake mp4")
        return 0, "", ""
    monkeypatch.setattr(ce, "_runner", runner)
    return runner


@pytest.fixture(autouse=True)
def _cleanup_runner():
    yield
    set_runner(None)


# ---------- load_storyboard ----------

def test_load_storyboard_basic(tmp_path):
    p = _make_storyboard_json(tmp_path, n_shots=3)
    sb = pl.load_storyboard(p)
    assert sb.title == "t"
    assert len(sb.shots) == 3
    assert sb.shots[0].description == "镜头 1 描述"


def test_load_storyboard_empty_shots(tmp_path):
    p = tmp_path / "sb.json"
    p.write_text(json.dumps({"title": "x", "shots": []}), encoding="utf-8")
    sb = pl.load_storyboard(p)
    assert sb.title == "x"
    assert len(sb.shots) == 0


# ---------- run_pipeline（mock backend） ----------

def test_pipeline_empty_shots_returns_error_result(tmp_path, fake_ffmpeg_runner, monkeypatch):
    """空 storyboard → run_pipeline 不抛，直接返回 error result。

    注：必须显式 mock backend，避免前一个 image_engine 测试 setenv MINIMAX_API_KEY
    污染环境导致 pipeline 误连真后端。
    """
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    sb = _make_storyboard(tmp_path, n_shots=0)
    result = pl.run_pipeline(
        sb, tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=te.MockTTSBackend(),
    )
    assert result.ok is False
    assert "video" in result.error.lower() or "shot" in result.error.lower()


def test_pipeline_mock_e2e_success(tmp_path, fake_ffmpeg_runner):
    sb = _make_storyboard(tmp_path, n_shots=2)
    result = pl.run_pipeline(
        sb,
        tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=te.MockTTSBackend(),
        keep_intermediate=False,
    )
    assert result.ok, f"errors: {result.error}; steps: {[s.name for s in result.steps]}"
    assert result.shot_count == 2
    assert result.ok_shots == 2
    assert result.bytes > 0
    assert (tmp_path / "out.mp4").exists()
    # 中间产物应被清理
    assert not result.intermediate_dir


def test_pipeline_image_failure_propagates(tmp_path, fake_ffmpeg_runner, monkeypatch):
    """image_backend 抛异常 → 该 shot 失败 → shot_meta.video 留 None → concat 阶段报错。"""

    class FailingImage(ie.ImageBackend):
        def generate(self, prompt, **opts):
            raise ie.ImageError("simulated image API failure")

    sb = _make_storyboard(tmp_path, n_shots=2)
    result = pl.run_pipeline(
        sb,
        tmp_path / "out.mp4",
        image_backend=FailingImage(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=te.MockTTSBackend(),
    )
    assert not result.ok
    assert result.failed_shots == 2
    assert "video" in result.error.lower() or "shot" in result.error.lower()


def test_pipeline_tts_failure_does_not_break_video(tmp_path, fake_ffmpeg_runner):
    """tts 失败非致命：视频还在，配音丢失。"""
    class FailingTTS(te.TTSBackend):
        def synth(self, text, output_path, **opts):
            raise te.TTSError("simulated tts failure")

    sb = _make_storyboard(tmp_path, n_shots=2)
    result = pl.run_pipeline(
        sb,
        tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=FailingTTS(),
    )
    # tts 失败不算致命；video 还在；concat 应成功（只有 video 无 audio）
    assert result.ok
    assert result.bytes > 0


def test_pipeline_skip_video(tmp_path, fake_ffmpeg_runner):
    """skip_video=True → 只跑 image + tts；没视频则 concat 报错。"""
    sb = _make_storyboard(tmp_path, n_shots=2)
    result = pl.run_pipeline(
        sb,
        tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=te.MockTTSBackend(),
        skip_video=True,
    )
    assert not result.ok
    assert "video" in result.error.lower()


def test_pipeline_keep_intermediate(tmp_path, fake_ffmpeg_runner):
    """keep_intermediate=True → intermediate_dir 字段有路径。"""
    sb = _make_storyboard(tmp_path, n_shots=2)
    result = pl.run_pipeline(
        sb,
        tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=te.MockTTSBackend(),
        keep_intermediate=True,
    )
    assert result.ok
    assert result.intermediate_dir  # 非空字符串


def test_pipeline_video_effect_cycle_changes(tmp_path, fake_ffmpeg_runner):
    """cycle effect 在 shots 间循环。"""
    sb = _make_storyboard(tmp_path, n_shots=4)
    effects_used = []
    real_render = ve.KenBurnsBackend.render

    def spy_render(self, image, output, **kwargs):
        effects_used.append(kwargs.get("effect"))
        return real_render(self, image, output, **kwargs)

    video_backend = ve.KenBurnsBackend()
    video_backend.render = spy_render.__get__(video_backend)

    pl.run_pipeline(
        sb,
        tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=video_backend,
        tts_backend=te.MockTTSBackend(),
        video_effect="cycle",
    )
    # cycle 下应该至少用了 3 个不同 effect
    unique = set(effects_used)
    assert len(unique) >= 3, f"cycle 没循环，effects={effects_used}"


def test_pipeline_video_effect_fixed(tmp_path, fake_ffmpeg_runner):
    """固定 effect 应一致传给所有 shots。"""
    sb = _make_storyboard(tmp_path, n_shots=3)
    effects_used = []
    real_render = ve.KenBurnsBackend.render

    def spy_render(self, image, output, **kwargs):
        effects_used.append(kwargs.get("effect"))
        return real_render(self, image, output, **kwargs)

    video_backend = ve.KenBurnsBackend()
    video_backend.render = spy_render.__get__(video_backend)

    pl.run_pipeline(
        sb,
        tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=video_backend,
        tts_backend=te.MockTTSBackend(),
        video_effect="zoom_in",
    )
    assert all(e == "zoom_in" for e in effects_used)


# ---------- PipelineResult dataclass ----------

def test_pipeline_result_to_dict(tmp_path, fake_ffmpeg_runner):
    sb = _make_storyboard(tmp_path, n_shots=2)
    result = pl.run_pipeline(
        sb, tmp_path / "out.mp4",
        image_backend=ie.MockImageBackend(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=te.MockTTSBackend(),
    )
    d = result.to_dict()
    assert "output" in d
    assert "shot_count" in d
    assert "steps" in d
    assert isinstance(d["steps"], list)


def test_pipeline_step_dataclass():
    step = pl.PipelineStep(name="image", index=1, ok=True, duration_seconds=1.5)
    assert step.name == "image"
    assert step.ok is True
    assert step.duration_seconds == 1.5


def test_pipeline_result_ok_property(tmp_path):
    r = pl.PipelineResult(output="/tmp/x.mp4", source="ok")
    assert r.ok is True
    r2 = pl.PipelineResult(output="/tmp/x.mp4", source="error")
    assert r2.ok is False


# ---------- V2.5 一致性：style_anchor + 角色外貌注入 ----------

def test_build_image_prompt_with_style_and_character():
    """style_anchor + 在场角色外貌注入 → 单 shot prompt。"""
    chars = [
        se.Character(name="农夫", appearance="宋代农夫", personality=[], color_palette=["灰褐"]),
        se.Character(name="兔子", appearance="灰色野兔", personality=[], color_palette=["灰"]),
    ]
    p = pl._build_image_prompt(
        shot_description="农夫在田间劳作",
        shot_char_names=["农夫"],
        all_characters=chars,
        style_anchor="古典水墨风格",
    )
    assert "古典水墨风格" in p
    assert "农夫" in p
    assert "宋代农夫" in p
    assert "灰褐" in p
    assert "田间劳作" in p
    # 兔子不应出现在 prompt（不在场）
    assert "兔子" not in p


def test_build_image_prompt_without_style_anchor():
    """没有 style_anchor 时不报错，且仍注入角色。"""
    chars = [se.Character(name="A", appearance="x", personality=[], color_palette=[])]
    p = pl._build_image_prompt(
        shot_description="场景描述", shot_char_names=["A"],
        all_characters=chars, style_anchor="",
    )
    assert "场景描述" in p
    assert "character A" in p


def test_build_image_prompt_no_characters():
    """没有角色时正常返回（只 style + scene）。"""
    p = pl._build_image_prompt(
        shot_description="纯风景", shot_char_names=[],
        all_characters=[], style_anchor="国画风格",
    )
    assert "国画风格" in p
    assert "纯风景" in p


def test_build_image_prompt_unknown_character_skipped():
    """shot.characters 引用了 storyboard.characters 里没有的角色 → 跳过。"""
    chars = [se.Character(name="农夫", appearance="宋代", personality=[], color_palette=[])]
    p = pl._build_image_prompt(
        shot_description="场景", shot_char_names=["农夫", "不存在的角色"],
        all_characters=chars, style_anchor="水墨",
    )
    assert "农夫" in p
    assert "不存在的角色" not in p


def test_build_image_prompt_style_anchor_order():
    """style_anchor 必须在最前（一致性基线）。"""
    chars = [se.Character(name="X", appearance="y", personality=[], color_palette=[])]
    p = pl._build_image_prompt(
        "scene", ["X"], chars, "STYLE_ANCHOR",
    )
    assert p.startswith("STYLE_ANCHOR"), f"style_anchor 不在前：{p[:80]}"


def test_build_image_prompt_consistent_for_same_character():
    """同一角色在两个 shot 的 character 行应一致（这是画风一致的关键）。"""
    chars = [se.Character(name="农夫", appearance="宋代农夫", personality=[], color_palette=["灰褐"])]
    p1 = pl._build_image_prompt("场景1", ["农夫"], chars, "水墨")
    p2 = pl._build_image_prompt("场景2", ["农夫"], chars, "水墨")
    # 两 prompt 中关于农夫的描述完全一致
    char_line_1 = "character 农夫 (宋代农夫), 配色 灰褐"
    char_line_2 = "character 农夫 (宋代农夫), 配色 灰褐"
    assert char_line_1 in p1
    assert char_line_2 in p2


def test_load_storyboard_reads_style_anchor(tmp_path):
    """storyboard.json 里的 style_anchor 字段能被正确读取。"""
    p = tmp_path / "sb.json"
    p.write_text(json.dumps({
        "title": "t", "shots": [], "characters": [],
        "style_anchor": "水墨风格",
    }), encoding="utf-8")
    sb = pl.load_storyboard(p)
    assert sb.style_anchor == "水墨风格"


def test_load_storyboard_missing_style_anchor_defaults_empty(tmp_path):
    """旧版 storyboard.json 没 style_anchor 字段 → 默认空串（不破坏兼容）。"""
    p = tmp_path / "sb.json"
    p.write_text(json.dumps({"title": "t", "shots": [], "characters": []}), encoding="utf-8")
    sb = pl.load_storyboard(p)
    assert sb.style_anchor == ""


def test_pipeline_uses_style_anchor_from_storyboard(tmp_path, fake_ffmpeg_runner, monkeypatch):
    """端到端：storyboard.style_anchor 被自动 prepend 到 image prompt。"""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    sb = _make_storyboard(tmp_path, n_shots=2)
    sb.style_anchor = "STYLE_TEST_ANCHOR_xxx"

    captured_prompts: list[str] = []

    class CaptureImage(ie.ImageBackend):
        def generate(self, prompt, **opts):
            captured_prompts.append(prompt)
            # 返回 fake JPEG bytes
            return [ie.ImageResult(
                bytes=ie._make_minimal_jpeg(),
                mime_type="image/jpeg",
                prompt=prompt,
                model="image-01",
                aspect_ratio="16:9",
                n=1,
            )]

    pl.run_pipeline(
        sb, tmp_path / "out.mp4",
        image_backend=CaptureImage(),
        video_backend=ve.KenBurnsBackend(),
        tts_backend=te.MockTTSBackend(),
    )

    assert len(captured_prompts) == 2
    for prompt in captured_prompts:
        assert "STYLE_TEST_ANCHOR_xxx" in prompt, f"style_anchor 未注入：{prompt[:80]}"
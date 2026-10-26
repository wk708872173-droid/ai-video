"""V4.0 Batch 1 — BUG-A / BUG-B：多镜 storyboard 的产物隔离与选片。

## 两个已确认的生产缺陷

**BUG-A（artifact_id 冲突）**：`Registry.add` 用 artifact_id 做 key
（`artifacts[id] = artifact`），而三个 agent 用了**固定 id**：
- `VideoSynthAgent` → `"video_out"`
- `TTSAgent` → `"tts_audio"` / `"tts_subtitle"`

→ 每个 shot 的产物**互相覆盖**，registry 里只剩最后一镜，`by_kind` 也因去重只剩一项。
实测：3 镜 storyboard → `by_kind_iter("video")` 只返回最后 1 镜。

**BUG-B（editing 选片）**：`_collect_video_paths_for_editing` 把 registry 里**所有**
kind="video" 的产物都收进来 → 同一镜的 motion 视频与 Ken Burns 兜底视频**同时**进列表
→ 成片里该镜出现两次。

## 为什么至今没暴露

现有 sample 产物都是**逐镜跑**（`scripts/run_story.sh` + `stitch_story.py`）——
每个 run 只有 1 镜，不存在冲突。受影响的是**主生产路径**：
`script` 产出 N 镜 storyboard → `pipeline` 一次跑完。

R4：所有测试都不打真 API / 不需要真 ffmpeg（用 mock backend + 临时文件）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.agents import VideoSynthAgent, TTSAgent  # noqa: E402
from hailuo_video.artifact import Artifact, Registry  # noqa: E402
from hailuo_video.orchestrator import PipelineAgent, PipelineConfig  # noqa: E402


# ---------- helpers ----------

def _touch(path: Path, size: int = 32) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


class _FakeVideoBackend:
    """最小 Ken Burns 替身：写一个假 mp4 并返回 ok。"""

    def __init__(self):
        self.calls: list[dict] = []

    def render(self, image_path, output_path, **opts):
        from hailuo_video.video_engine import VideoResult
        self.calls.append({"image_path": str(image_path), "output_path": str(output_path), **opts})
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 32)
        return VideoResult(
            output_path=str(out), ok=True, effect=opts.get("effect", "zoom_in"),
            seconds=opts.get("seconds", 5), fps=opts.get("fps", 8), error="",
        )


class _FakeTTSResult:
    def __init__(self, sub_file: str = ""):
        self.sub_file = sub_file


# ============================================================
# BUG-A：artifact_id 按 shot 隔离
# ============================================================

def test_video_synth_artifact_id_is_per_shot(tmp_path):
    """两个 shot 的 Ken Burns 视频必须是不同 artifact_id。"""
    backend = _FakeVideoBackend()
    agent = VideoSynthAgent()
    ids = []
    for shot_idx in (0, 1, 2):
        img = _touch(tmp_path / f"img_{shot_idx}.jpg")
        out = tmp_path / f"shot_{shot_idx:03d}_kenburns.mp4"
        res = agent.execute({
            "image_path": str(img),
            "output_path": str(out),
            "video_backend": backend,
            "shot_idx": shot_idx,
        })
        assert res.ok, res.error
        ids.append(res.artifacts[0].artifact_id)
    assert len(set(ids)) == 3, f"artifact_id 未按 shot 隔离：{ids}"


def test_video_synth_registry_keeps_all_shots(tmp_path):
    """注册到 registry 后，3 个 shot 的视频必须都在（不再只剩 1 个）。"""
    backend = _FakeVideoBackend()
    agent = VideoSynthAgent()
    reg = Registry()
    for shot_idx in (0, 1, 2):
        img = _touch(tmp_path / f"img_{shot_idx}.jpg")
        out = _touch(tmp_path / f"shot_{shot_idx:03d}_kenburns.mp4")
        res = agent.execute({
            "image_path": str(img),
            "output_path": str(out),
            "video_backend": backend,
            "shot_idx": shot_idx,
        })
        for art in res.artifacts:
            reg.add(art, compute_hash=False)
    videos = reg.by_kind_iter("video")
    assert len(videos) == 3, f"registry 只剩 {len(videos)} 个视频（应为 3）"
    assert {v.metadata["shot_idx"] for v in videos} == {0, 1, 2}


def test_tts_artifact_id_is_per_shot(tmp_path):
    """两个 shot 的配音必须是不同 artifact_id。"""
    from hailuo_video import tts_engine as te

    backend = te.MockTTSBackend()
    agent = TTSAgent()
    ids = []
    for shot_idx in (0, 1):
        res = agent.execute({
            "text": "汪",
            "output": str(tmp_path / f"shot_{shot_idx:03d}.mp3"),
            "tts_backend": backend,
            "shot_idx": shot_idx,
            "subtitle": str(tmp_path / f"shot_{shot_idx:03d}.vtt"),
        })
        assert res.ok, res.error
        ids.extend(a.artifact_id for a in res.artifacts)
    assert len(set(ids)) == len(ids), f"TTS artifact_id 未按 shot 隔离：{ids}"


def test_tts_registry_keeps_all_shots(tmp_path):
    """注册后所有 shot 的音频都在（此前只剩最后一镜）。"""
    from hailuo_video import tts_engine as te

    backend = te.MockTTSBackend()
    agent = TTSAgent()
    reg = Registry()
    for shot_idx in (0, 1, 2):
        res = agent.execute({
            "text": f"第{shot_idx}镜",
            "output": str(tmp_path / f"shot_{shot_idx:03d}.mp3"),
            "tts_backend": backend,
            "shot_idx": shot_idx,
        })
        for art in res.artifacts:
            reg.add(art, compute_hash=False)
    audios = reg.by_kind_iter("audio")
    assert len(audios) == 3, f"registry 只剩 {len(audios)} 段音频（应为 3）"


def test_scoped_id_falls_back_to_filename(tmp_path):
    """没有 shot_idx 时退回文件名（仍然唯一，不覆盖）。"""
    from hailuo_video.agents import _scoped_id

    a = _scoped_id("video", None, tmp_path / "shot_000_kenburns.mp4")
    b = _scoped_id("video", None, tmp_path / "shot_001_kenburns.mp4")
    assert a != b


# ============================================================
# BUG-B：editing 每镜只选一段（motion 优先）
# ============================================================

def _registry_with(tmp_path, *, motion_shots=(0,), kenburns_shots=(0, 1, 2)):
    reg = Registry()
    for s in kenburns_shots:
        p = _touch(tmp_path / f"shot_{s:03d}_kenburns.mp4")
        reg.add(Artifact(
            artifact_id=f"video_{s}", kind="video", path=p, producer="video_synth",
            metadata={"shot_idx": s},
        ), compute_hash=False)
    for s in motion_shots:
        p = _touch(tmp_path / f"shot_{s:03d}_motion.mp4")
        reg.add(Artifact(
            artifact_id=f"motion_{s}", kind="video", path=p, producer="motion_gen",
            metadata={"shot_idx": s},
        ), compute_hash=False)
    return reg


def test_editing_picks_one_video_per_shot(tmp_path):
    """3 个 shot → 3 段视频（不是 4 段：motion 与 kenburns 不该同时进）。"""
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    agent.registry = _registry_with(tmp_path, motion_shots=(0,), kenburns_shots=(0, 1, 2))
    paths = agent._collect_video_paths_for_editing({}, 0)
    assert len(paths) == 3, f"每镜应只选一段，实际 {len(paths)}：{paths}"


def test_editing_prefers_motion_over_kenburns(tmp_path):
    """有 motion 的镜头优先用 motion（真 AI 视频），不是 Ken Burns 兜底。"""
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    agent.registry = _registry_with(tmp_path, motion_shots=(0,), kenburns_shots=(0, 1, 2))
    paths = agent._collect_video_paths_for_editing({}, 0)
    assert "shot_000_motion.mp4" in paths[0]
    assert "shot_000_kenburns.mp4" not in paths


def test_editing_orders_by_shot_index(tmp_path):
    """拼接顺序必须按 shot_idx（时间顺序）。"""
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    agent.registry = _registry_with(tmp_path, motion_shots=(), kenburns_shots=(2, 0, 1))
    paths = agent._collect_video_paths_for_editing({}, 0)
    assert [Path(p).name for p in paths] == [
        "shot_000_kenburns.mp4",
        "shot_001_kenburns.mp4",
        "shot_002_kenburns.mp4",
    ]


def test_editing_falls_back_to_kenburns_when_no_motion(tmp_path):
    """没有 motion 的镜头用 Ken Burns 兜底。"""
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    agent.registry = _registry_with(tmp_path, motion_shots=(), kenburns_shots=(0, 1))
    paths = agent._collect_video_paths_for_editing({}, 0)
    assert all("kenburns" in p for p in paths)


def test_editing_infers_shot_idx_from_artifact_id(tmp_path):
    """老数据没有 shot_idx metadata 时，按 artifact_id 里的数字兜底。"""
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    reg = Registry()
    for s in (0, 1):
        p = _touch(tmp_path / f"shot_{s:03d}.mp4")
        reg.add(Artifact(
            artifact_id=f"motion_{s}", kind="video", path=p, producer="motion_gen",
            metadata={},
        ), compute_hash=False)
    agent.registry = reg
    paths = agent._collect_video_paths_for_editing({}, 0)
    assert len(paths) == 2
    assert [Path(p).name for p in paths] == ["shot_000.mp4", "shot_001.mp4"]

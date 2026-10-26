"""V4.0 Batch 2 — W-D：TTS 配音与字幕必须进成片（BUG-C）。

## 已确认的缺陷

V3 的 editing 链路只处理画面：

```
color_normalize → crossfade(xfade) → LUT
```

**TTS 的配音与字幕从未进入成片**。实测仓库里已交付的成片（用 ffprobe 查音轨）：

```
★无音轨★  sample/story_arc_qingdao_autumn/final/qingdao_autumn_day1_9x16_xiaohongshu.mp4
★无音轨★  sample/story_arc_xiaohei_solo/final/xiaohei_2days_9x16_xiaohongshu.mp4
有音轨      sample/story_arc_xiaohei_solo/final/xiaohei_3days_16x9.mp4   ← 另一条脚本产的
```

即 9 个成片里 8 个无音轨（含客户主推的小红书 9:16 版本）。TTS phase 跑完了，
产物被丢在 registry 里没人消费。

## 为什么在"每镜"层面合并

- 每镜的 .vtt 时间戳是**相对该镜**的；整片烧字幕要平移到拼接后时间轴
  （还要算 xfade 的 0.25s 重叠）→ 极易算错。每镜烧则天然正确。
- 每镜合并后带上音轨 → `crossfade_clips` 的 `acrossfade` 就能把音轨叠起来
  （该能力本来就有，只是此前没输入带音轨的视频）。

R4：纯函数测试不碰 ffmpeg；涉及 ffmpeg 的用例用注入的 runner 替身。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video.editing.merge import ensure_srt, merge_shot_media, vtt_to_srt  # noqa: E402


VTT = """WEBVTT

00:00:00.000 --> 00:00:02.500
第一句台词

00:00:02.500 --> 00:00:05.000
第二句台词
"""


def _sb(n: int):
    """最小 Storyboard（_build_task 遇到 shot=None 会提前返回，所以要用真 Shot）。"""
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard
    return Storyboard(
        title="t", characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=i + 1, scene_id=1, shot_type="中景", description="d")
               for i in range(n)],
    )


# ============================================================
# vtt → srt 转换
# ============================================================

def test_vtt_to_srt_converts_timestamps():
    out = vtt_to_srt(VTT)
    assert "00:00:00,000 --> 00:00:02,500" in out
    assert "WEBVTT" not in out


def test_vtt_to_srt_numbers_cues():
    out = vtt_to_srt(VTT)
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert lines[0] == "1"
    assert "2" in lines


def test_vtt_to_srt_keeps_text():
    out = vtt_to_srt(VTT)
    assert "第一句台词" in out
    assert "第二句台词" in out


def test_vtt_to_srt_handles_empty():
    assert vtt_to_srt("") == ""
    assert vtt_to_srt("WEBVTT\n\n") == ""


def test_vtt_to_srt_skips_note_blocks():
    out = vtt_to_srt("WEBVTT\n\nNOTE 这是注释\n\n00:00:00.000 --> 00:00:01.000\n台词\n")
    assert "NOTE" not in out
    assert "这是注释" not in out
    assert "台词" in out


def test_ensure_srt_converts_vtt_file(tmp_path):
    vtt = tmp_path / "a.vtt"
    vtt.write_text(VTT, encoding="utf-8")
    srt = ensure_srt(vtt, tmp_path)
    assert srt.suffix == ".srt"
    assert "00:00:00,000" in srt.read_text(encoding="utf-8")


def test_ensure_srt_keeps_srt_file(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    assert ensure_srt(srt, tmp_path) == srt


# ============================================================
# merge_clip 的 shortest 语义
# ============================================================

def test_merge_clip_supports_non_truncating_mode():
    """旁白场景必须能用 shortest=False（否则 6s 画面被 3s 旁白截成 3s）。"""
    import inspect
    from hailuo_video import concat_engine

    sig = inspect.signature(concat_engine.merge_clip)
    assert "shortest" in sig.parameters
    assert sig.parameters["shortest"].default is True, "默认语义必须保持（向后兼容）"


def test_merge_clip_non_shortest_omits_shortest_flag(monkeypatch):
    from hailuo_video import concat_engine

    captured: dict = {}

    def fake_runner(args, timeout):
        captured["args"] = list(args)
        return 0, "", ""

    monkeypatch.setattr(concat_engine, "_runner", fake_runner)
    monkeypatch.setattr(concat_engine, "_resolve_ffmpeg_exe", lambda: "ffmpeg")
    concat_engine.merge_clip("v.mp4", "a.mp3", "o.mp4", shortest=False)
    assert "-shortest" not in captured["args"]

    captured.clear()
    concat_engine.merge_clip("v.mp4", "a.mp3", "o.mp4")
    assert "-shortest" in captured["args"]


# ============================================================
# 每镜合并（用注入的 ffmpeg runner，不打真 ffmpeg）
# ============================================================

def test_merge_shot_media_without_audio_or_subtitle(tmp_path):
    """无音频无字幕 → 直接复制画面。"""
    from hailuo_video import editing

    src = tmp_path / "shot.mp4"
    src.write_bytes(b"\x00" * 16)
    out = tmp_path / "out.mp4"

    calls: list[str] = []

    def fake_burn(video, subtitle, output, **kw):
        calls.append("burn")
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    def fake_merge(video, audio, output, **kw):
        calls.append("merge")
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(editing.merge, "burn_subtitles", fake_burn)
    monkeypatch.setattr(editing.merge, "merge_clip", fake_merge)
    try:
        merge_shot_media(src, out, work_dir=tmp_path / "w")
    finally:
        monkeypatch.undo()

    assert calls == []
    assert out.is_file()


def test_merge_shot_media_burns_then_merges(tmp_path, monkeypatch):
    """顺序：先烧字幕，再混旁白。"""
    from hailuo_video import editing

    src = tmp_path / "shot.mp4"
    src.write_bytes(b"\x00" * 16)
    vtt = tmp_path / "shot.vtt"
    vtt.write_text(VTT, encoding="utf-8")
    mp3 = tmp_path / "shot.mp3"
    mp3.write_bytes(b"\x00" * 16)
    out = tmp_path / "out.mp4"

    calls: list[str] = []

    def fake_burn(video, subtitle, output, **kw):
        calls.append(f"burn:{Path(subtitle).suffix}")
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    def fake_merge(video, audio, output, **kw):
        calls.append(f"merge:shortest={kw.get('shortest')}")
        Path(output).write_bytes(b"\x00" * 16)
        return Path(output)

    monkeypatch.setattr(editing.merge, "burn_subtitles", fake_burn)
    monkeypatch.setattr(editing.merge, "merge_clip", fake_merge)

    merge_shot_media(src, out, audio=mp3, subtitle=vtt, work_dir=tmp_path / "w")

    assert calls[0].startswith("burn:"), calls
    assert calls[0].endswith(".srt"), "vtt 必须先转成 srt"
    assert calls[1].startswith("merge:"), calls
    assert "shortest=False" in calls[1], "旁白合并不得截断画面"
    assert out.is_file()


# ============================================================
# orchestrator：editing task 必须带上 audio_paths / subtitle_paths
# ============================================================

def test_editing_task_carries_audio_and_subtitle(tmp_path):
    """editing task 必须带上每镜的旁白 / 字幕（否则成片无配音无字幕）。"""
    from hailuo_video.artifact import Artifact, Registry
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    reg = Registry()
    for s in (0, 1):
        v = tmp_path / f"shot_{s}.mp4"
        v.write_bytes(b"\x00")
        reg.add(Artifact(artifact_id=f"video_{s}", kind="video", path=v,
                         producer="video_synth", metadata={"shot_idx": s}),
                compute_hash=False)
        a = tmp_path / f"shot_{s}.mp3"
        a.write_bytes(b"\x00")
        reg.add(Artifact(artifact_id=f"tts_audio_{s}", kind="audio", path=a,
                         producer="tts", metadata={"shot_idx": s}), compute_hash=False)
        sub = tmp_path / f"shot_{s}.vtt"
        sub.write_text(VTT, encoding="utf-8")
        reg.add(Artifact(artifact_id=f"tts_subtitle_{s}", kind="subtitle", path=sub,
                         producer="tts", metadata={"shot_idx": s}), compute_hash=False)
    agent.registry = reg

    sb = _sb(2)
    ctx = {"storyboard": sb, "shot_count": 2, "intermediate_dir": tmp_path}
    task = agent._build_task("editing", 0, ctx, None)

    assert len(task["audio_paths"]) == 2
    assert len(task["subtitle_paths"]) == 2
    assert task["audio_paths"][0].endswith("shot_0.mp3")
    assert task["audio_paths"][1].endswith("shot_1.mp3")


def test_audio_list_is_index_aligned_with_missing_shot(tmp_path):
    """某镜没有旁白时，列表必须**下标对齐**（留 None），不能紧凑排列。

    否则 shot 1 的旁白会被安到 shot 0 上（张冠李戴）。
    """
    from hailuo_video.artifact import Artifact, Registry
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    reg = Registry()
    for s in (0, 1):
        v = tmp_path / f"shot_{s}.mp4"
        v.write_bytes(b"\x00")
        reg.add(Artifact(artifact_id=f"video_{s}", kind="video", path=v,
                         producer="video_synth", metadata={"shot_idx": s}),
                compute_hash=False)
    # 只有 shot 1 有旁白
    a = tmp_path / "shot_1.mp3"
    a.write_bytes(b"\x00")
    reg.add(Artifact(artifact_id="tts_audio_1", kind="audio", path=a,
                     producer="tts", metadata={"shot_idx": 1}), compute_hash=False)
    agent.registry = reg

    sb = _sb(2)
    ctx = {"storyboard": sb, "shot_count": 2, "intermediate_dir": tmp_path}
    task = agent._build_task("editing", 0, ctx, None)

    assert len(task["audio_paths"]) == 2
    assert task["audio_paths"][0] is None, "shot 0 没有旁白 → 必须留 None"
    assert task["audio_paths"][1] is not None


# ============================================================
# 真实 ffmpeg 端到端（证据等级 a：真 ffmpeg + 观察到的产物）
# ============================================================

def _ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def test_real_ffmpeg_final_has_audio_track(tmp_path):
    """真 ffmpeg：editing 链路的成片**必须有音轨**。

    这是 BUG-C 的直接回归护栏 —— 旧行为下这条断言会失败（成片无音轨）。
    需要真 ffmpeg；环境没有时跳过（CI 装了 imageio-ffmpeg → 会真跑）。
    """
    import re
    import subprocess

    exe = _ffmpeg_exe()
    if not exe:
        pytest.skip("需要 ffmpeg")

    vids, mp3s, vtts = [], [], []
    for i in range(2):
        color = "0x%06x" % (0x884400 + i * 0x112233)
        v = tmp_path / f"shot_{i}.mp4"
        subprocess.run(
            [exe, "-y", "-f", "lavfi", "-i",
             "color=c=" + color + ":size=320x180:rate=8:d=3",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)],
            capture_output=True, check=True)
        vids.append(v)

        a = tmp_path / f"shot_{i}.mp3"
        subprocess.run(
            [exe, "-y", "-f", "lavfi", "-i",
             "sine=frequency=%d:duration=3" % (440 + i * 220), str(a)],
            capture_output=True, check=True)
        mp3s.append(a)

        s = tmp_path / f"shot_{i}.vtt"
        s.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:03.000\n第%d镜\n" % i,
                     encoding="utf-8")
        vtts.append(s)

    from hailuo_video.editing.full import full_editing_pipeline

    out = tmp_path / "final.mp4"
    full_editing_pipeline(
        [str(v) for v in vids], str(out),
        audio_paths=[str(a) for a in mp3s],
        subtitle_paths=[str(v) for v in vtts],
        work_dir=tmp_path / "work",
    )
    assert out.is_file()

    probe = subprocess.run([exe, "-i", str(out)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    err = probe.stderr or ""
    assert "Audio:" in err, "成片没有音轨 —— BUG-C 回归"

    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", err)
    assert m, err[:400]
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    # 3s x 2 - 0.25s xfade 重叠 ≈ 5.75s
    assert 5.0 <= dur <= 6.2, f"成片时长异常：{dur}（画面可能被旁白截断）"


def test_real_ffmpeg_control_has_no_audio(tmp_path):
    """对照组：不传 audio_paths → 成片无音轨（这就是 BUG-C 的旧行为）。"""
    import subprocess

    exe = _ffmpeg_exe()
    if not exe:
        pytest.skip("需要 ffmpeg")

    v = tmp_path / "shot.mp4"
    subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i", "color=c=0x884400:size=320x180:rate=8:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)],
        capture_output=True, check=True)

    from hailuo_video.editing.full import full_editing_pipeline

    out = tmp_path / "final_noav.mp4"
    full_editing_pipeline([str(v)], str(out), work_dir=tmp_path / "work")
    probe = subprocess.run([exe, "-i", str(out)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    assert "Audio:" not in (probe.stderr or "")

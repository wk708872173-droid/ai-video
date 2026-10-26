"""V4.0 手工验证：真实 ffmpeg 下，editing 链路是否把旁白合进成片。

（这是给开发者跑的一次性脚本，不是测试 —— 测试用替身 runner。）
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "sandbox")
import imageio_ffmpeg

exe = imageio_ffmpeg.get_ffmpeg_exe()
td = Path(tempfile.mkdtemp(prefix="v40_av_"))

vids, mp3s, vtts = [], [], []
for i in range(2):
    color = "0x%06x" % (0x884400 + i * 0x112233)
    v = td / f"shot_{i}.mp4"
    subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i",
         "color=c=" + color + ":size=320x180:rate=8:d=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)],
        capture_output=True, check=True)
    vids.append(v)

    a = td / f"shot_{i}.mp3"
    subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i",
         "sine=frequency=%d:duration=3" % (440 + i * 220), str(a)],
        capture_output=True, check=True)
    mp3s.append(a)

    s = td / f"shot_{i}.vtt"
    s.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\n第%d镜字幕\n" % i,
        encoding="utf-8")
    vtts.append(s)


def probe(path):
    p = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    err = p.stderr or ""
    m = re.search(r"Duration: (\d+:\d+:\d+\.\d+)", err)
    return ("Audio:" in err), (m.group(1) if m else "?")


from hailuo_video.editing.full import full_editing_pipeline

out = td / "final.mp4"
res = full_editing_pipeline(
    [str(v) for v in vids], str(out),
    audio_paths=[str(a) for a in mp3s],
    subtitle_paths=[str(v) for v in vtts],
    work_dir=td / "work",
)
print("pipeline ok:", res.final_path)
has_audio, dur = probe(out)
print("成片（V4.0 新路径）  音轨:", "YES" if has_audio else "NO", " 时长:", dur)
print("  期望: 3s x 2 - 0.25s xfade 重叠 = 5.75s，且有音轨")

out2 = td / "final_old.mp4"
full_editing_pipeline([str(v) for v in vids], str(out2), work_dir=td / "work2")
has2, dur2 = probe(out2)
print("对照组（不传 audio_paths = 旧行为）  音轨:", "YES" if has2 else "NO", " 时长:", dur2)
print("workdir:", td)

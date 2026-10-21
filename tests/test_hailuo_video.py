"""REQ-LV 本地视频推理（CogVideoX）单测。全部 mock，不依赖 torch / diffusers / HF 下载。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 把 sandbox/ 注入 sys.path，让 `from hailuo_video.xxx import` 可解析
SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import local_client  # noqa: E402
from hailuo_video import state as state_mod  # noqa: E402
from hailuo_video import calllog as calllog_mod  # noqa: E402
from hailuo_video import doctor as doctor_mod  # noqa: E402
from hailuo_video.batch import load_prompts, run_batch  # noqa: E402
from hailuo_video.cli import main as cli_main  # noqa: E402
from hailuo_video.manifest import MANIFEST_HEADER, write_manifest  # noqa: E402


# ---------- 环境 fixture：env 干净 + 默认 cfg ----------

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """每个测试一份干净 env + 默认 cfg + 清空注入。"""
    for key in local_client._DOTENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    local_client.reset_overrides()
    yield
    local_client.reset_overrides()


def _enable_overrides(tmp_path: Path):
    """装上默认 fake loader+caller：load 返回 sentinel，call 写 8 字节 mp4。"""
    sentinel = object()

    def fake_load(model, device, dtype, cache_dir):
        return sentinel

    def fake_call(pipeline, prompt, num_frames, height, width, num_steps, guidance_scale, seed, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\x00\x01\x02 fake mp4")  # 12 bytes

    local_client.set_overrides(load=fake_load, call=fake_call)
    return sentinel


# ---------- 配置门禁 ----------

def test_config_dict_has_all_keys():
    cfg = local_client.config_dict()
    for k in ("model", "device", "dtype", "num_frames", "height", "width",
              "steps", "guidance", "seed", "cache_dir"):
        assert k in cfg


def test_env_overrides_take_precedence(monkeypatch):
    monkeypatch.setenv("LOCAL_VIDEO_MODEL", "foo/bar")
    monkeypatch.setenv("LOCAL_VIDEO_DEVICE", "cuda")
    monkeypatch.setenv("LOCAL_VIDEO_STEPS", "20")
    assert local_client.model_id() == "foo/bar"
    assert local_client.device() == "cuda"
    assert local_client.steps() == 20


def test_invalid_int_env_falls_back(monkeypatch):
    monkeypatch.setenv("LOCAL_VIDEO_STEPS", "not-a-number")
    assert local_client.steps() == local_client.DEFAULT_STEPS


def test_invalid_float_env_falls_back(monkeypatch):
    monkeypatch.setenv("LOCAL_VIDEO_GUIDANCE", "bad")
    assert local_client.guidance() == local_client.DEFAULT_GUIDANCE


def test_warnings_bf16_on_cpu(monkeypatch):
    monkeypatch.setenv("LOCAL_VIDEO_DEVICE", "cpu")
    monkeypatch.setenv("LOCAL_VIDEO_DTYPE", "bfloat16")
    warns = local_client.warnings()
    assert any("bfloat16" in w for w in warns)


def test_warnings_invalid_device(monkeypatch):
    monkeypatch.setenv("LOCAL_VIDEO_DEVICE", "gpu")
    warns = local_client.warnings()
    assert any("gpu" in w for w in warns)


def test_warnings_clean_on_defaults():
    assert local_client.warnings() == []


# ---------- generate：基础 ----------

def test_generate_rejects_empty_prompt(tmp_path):
    res = local_client.generate("", tmp_path / "out.mp4")
    assert res.ok is False
    assert res.source == "disabled"


def test_generate_rejects_whitespace_prompt(tmp_path):
    res = local_client.generate("   ", tmp_path / "out.mp4")
    assert res.ok is False
    assert res.source == "disabled"


def test_generate_bf16_on_cpu_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_VIDEO_DTYPE", "bfloat16")
    monkeypatch.setenv("LOCAL_VIDEO_DEVICE", "cpu")
    _enable_overrides(tmp_path)
    res = local_client.generate("hello", tmp_path / "out.mp4")
    assert res.ok is False
    assert res.source == "disabled"
    assert "bfloat16" in res.error


def test_generate_no_env_no_override_returns_error(tmp_path, monkeypatch):
    """无覆盖 + 环境未就绪 → 报错 + 提示安装。"""
    monkeypatch.setattr(local_client, "torch_available", lambda: False)
    monkeypatch.setattr(local_client, "diffusers_available", lambda: False)
    res = local_client.generate("hello", tmp_path / "out.mp4")
    assert res.ok is False
    assert res.source == "error"
    assert "torch" in res.error or "diffusers" in res.error


# ---------- generate：注入覆盖（成功路径）----------

def test_generate_with_override_success(tmp_path):
    _enable_overrides(tmp_path)
    out = tmp_path / "out.mp4"
    res = local_client.generate("a cat", out)
    assert res.ok is True
    assert res.source == "ok"
    assert res.run_id != ""
    assert out.exists()
    assert res.bytes == 12


def test_generate_populates_state_fields(tmp_path):
    _enable_overrides(tmp_path)
    out = tmp_path / "out.mp4"
    res = local_client.generate("hello", out, num_frames=9)
    assert res.num_frames == 9
    assert res.model == local_client.DEFAULT_MODEL
    assert res.device == local_client.DEFAULT_DEVICE
    assert res.duration_seconds >= 0.0


def test_generate_override_call_exception_returns_error(tmp_path):
    def boom_load(model, device, dtype, cache_dir):
        return object()

    def boom_call(pipeline, prompt, nf, h, w, ns, gs, seed, out):
        raise RuntimeError("simulated OOM")

    local_client.set_overrides(load=boom_load, call=boom_call)
    res = local_client.generate("hello", tmp_path / "out.mp4")
    assert res.ok is False
    assert res.source == "error"
    assert "RuntimeError" in res.error
    assert "simulated OOM" in res.error


# ---------- 日志：runs.jsonl 不泄漏 env 值 ----------

def test_runs_jsonl_written_on_each_generate(tmp_path):
    _enable_overrides(tmp_path)
    local_client.generate("hello", tmp_path / "a.mp4")
    import json
    line = local_client.SANDBOX_RUNTIME.joinpath("runs.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    record = json.loads(line)
    assert record["op"] == "inference"
    assert record["ok"] is True
    assert record["source"] == "ok"
    assert record["extra"]["prompt_chars"] == 5


def test_runs_jsonl_does_not_leak_cache_dir_value(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_VIDEO_CACHE_DIR", "/secret/path/to/cache")
    _enable_overrides(tmp_path)
    local_client.generate("hello", tmp_path / "a.mp4")
    log = local_client.SANDBOX_RUNTIME.joinpath("runs.jsonl").read_text(encoding="utf-8")
    assert "/secret/path/to/cache" not in log
    assert "LOCAL_VIDEO_CACHE_DIR" not in log


def test_generate_writes_to_custom_runtime_dir(tmp_path):
    _enable_overrides(tmp_path)
    rt = tmp_path / "rt"
    res = local_client.generate("hello", tmp_path / "a.mp4", runtime_dir=rt)
    assert res.ok is True
    assert (rt / "runs.jsonl").is_file()


# ---------- state 持久化 ----------

def test_state_load_missing_returns_empty(tmp_path):
    s = state_mod.load_state(tmp_path / "nope.json")
    assert s.last_run_id == ""
    assert s.last_status == ""


def test_state_round_trip(tmp_path):
    p = tmp_path / "state.json"
    state_mod.update_last(
        run_id="r_123", prompt="hi", output_path="/out/x.mp4", status="succeeded",
        model="THUDM/CogVideoX-2b", device="cpu", num_frames=49,
        duration_seconds=1.2, path=p,
    )
    s = state_mod.load_state(p)
    assert s.last_run_id == "r_123"
    assert s.last_prompt == "hi"
    assert s.last_output_path == "/out/x.mp4"
    assert s.last_status == "succeeded"
    assert s.last_model == "THUDM/CogVideoX-2b"
    assert s.last_device == "cpu"
    assert s.last_num_frames == 49
    assert s.last_duration_seconds == 1.2
    assert s.last_updated != ""


def test_state_atomic_write_no_tempfile_left(tmp_path):
    p = tmp_path / "state.json"
    state_mod.update_last(run_id="t", path=p)
    leftovers = [x.name for x in tmp_path.iterdir() if x.name.startswith(".state-")]
    assert leftovers == [], f"原子写残留: {leftovers}"


def test_state_update_last_is_partial_overwrite(tmp_path):
    p = tmp_path / "state.json"
    state_mod.update_last(run_id="t1", prompt="p1", status="succeeded", model="m1", path=p)
    state_mod.update_last(run_id="t2", path=p)  # 只更新 run_id
    s = state_mod.load_state(p)
    assert s.last_run_id == "t2"
    assert s.last_prompt == "p1"
    assert s.last_status == "succeeded"
    assert s.last_model == "m1"


def test_state_corrupt_json_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not valid json", encoding="utf-8")
    s = state_mod.load_state(p)
    assert s.last_run_id == ""


def test_state_from_dict_ignores_unknown_keys(tmp_path):
    import json
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_run_id": "t1", "last_future_field": "x"}), encoding="utf-8")
    s = state_mod.load_state(p)
    assert s.last_run_id == "t1"


# ---------- calllog 解析 ----------

def test_calllog_load_missing_returns_empty(tmp_path):
    assert calllog_mod.load_calls(tmp_path) == []


def test_calllog_tail_filters_by_op(tmp_path):
    import json
    rt = tmp_path
    (rt / "runs.jsonl").write_text(
        json.dumps({"ts": "t1", "op": "inference", "ok": True, "source": "ok"}) + "\n"
        + json.dumps({"ts": "t2", "op": "inference", "ok": True, "source": "ok"}) + "\n",
        encoding="utf-8",
    )
    recs = calllog_mod.tail_calls(n=10, op="inference", runtime_dir=rt)
    assert len(recs) == 2
    assert all(r.op == "inference" for r in recs)


def test_calllog_tail_respects_n(tmp_path):
    import json
    rt = tmp_path
    lines = [json.dumps({"ts": f"t{i}", "op": "inference", "ok": True, "source": "ok"}) for i in range(5)]
    (rt / "runs.jsonl").write_text("\n".join(lines), encoding="utf-8")
    assert len(calllog_mod.tail_calls(n=3, runtime_dir=rt)) == 3


def test_calllog_format_marks_ok_and_error():
    r_ok = calllog_mod.CallRecord(ts="t", op="inference", ok=True, source="ok")
    r_err = calllog_mod.CallRecord(ts="t", op="inference", ok=False, source="error", error="OOM")
    out = calllog_mod.format_calls([r_ok, r_err])
    assert "✓" in out
    assert "✗" in out
    assert "OOM" in out


# ---------- doctor ----------

def test_diagnose_no_torch(monkeypatch):
    monkeypatch.setattr(local_client, "torch_available", lambda: False)
    monkeypatch.setattr(local_client, "diffusers_available", lambda: False)
    d = doctor_mod.diagnose()
    assert d["configured"] is False
    checks = {c["check"]: c for c in d["checks"]}
    assert checks["torch_installed"]["ok"] is False
    assert checks["diffusers_installed"]["ok"] is False


def test_diagnose_no_cuda_when_requested(monkeypatch):
    monkeypatch.setattr(local_client, "torch_available", lambda: True)
    monkeypatch.setattr(local_client, "diffusers_available", lambda: True)
    monkeypatch.setenv("LOCAL_VIDEO_DEVICE", "cuda")
    monkeypatch.setattr(local_client, "device", lambda: "cuda")
    import types
    fake_torch = types.SimpleNamespace(
        __version__="2.5.0",
        cuda=types.SimpleNamespace(is_available=lambda: False, device_count=lambda: 0, get_device_name=lambda i: ""),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    d = doctor_mod.diagnose()
    device_check = next(c for c in d["checks"] if c["check"] == "device")
    assert device_check["ok"] is False
    assert "cuda" in device_check["detail"]


def test_diagnose_bf16_cpu_dtype_fail(monkeypatch):
    monkeypatch.setattr(local_client, "torch_available", lambda: True)
    monkeypatch.setattr(local_client, "diffusers_available", lambda: True)
    monkeypatch.setenv("LOCAL_VIDEO_DEVICE", "cpu")
    monkeypatch.setenv("LOCAL_VIDEO_DTYPE", "bfloat16")
    d = doctor_mod.diagnose()
    dtype_check = next(c for c in d["checks"] if c["check"] == "dtype")
    assert dtype_check["ok"] is False
    assert "bfloat16" in dtype_check["detail"]


def test_format_diagnose_unconfigured():
    d = {"configured": False, "warnings": [], "checks": [{"check": "torch_installed", "ok": False, "detail": "未装"}]}
    out = doctor_mod.format_diagnose(d)
    assert "configured=False" in out
    assert "未装" in out


def test_format_diagnose_mixed_checks():
    d = {
        "configured": True,
        "warnings": [],
        "checks": [
            {"check": "torch_installed", "ok": True, "detail": "ok"},
            {"check": "diffusers_installed", "ok": False, "detail": "缺"},
        ],
    }
    out = doctor_mod.format_diagnose(d)
    assert "✓" in out and "✗" in out


# ---------- CLI ----------

def test_cli_ping_prints_config(capsys):
    rc = cli_main(["ping"])
    out = capsys.readouterr()
    assert rc in (0, 2)  # 取决于环境
    assert "model" in out.err
    assert "device" in out.err


def test_cli_generate_fails_when_unready(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(local_client, "torch_available", lambda: False)
    monkeypatch.setattr(local_client, "diffusers_available", lambda: False)
    rc = cli_main(["generate", "a cat", "-o", str(tmp_path / "out.mp4")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "环境未就绪" in err


def test_cli_generate_disabled_empty_prompt(capsys, monkeypatch, tmp_path):
    _enable_overrides(tmp_path)
    monkeypatch.setenv("LOCAL_VIDEO_DEVICE", "cpu")
    rc = cli_main(["generate", "", "-o", str(tmp_path / "out.mp4")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "disabled" in err or "prompt 为空" in err


def test_cli_generate_success_path(monkeypatch, tmp_path, capsys):
    _enable_overrides(tmp_path)
    out = tmp_path / "out.mp4"
    rc = cli_main(["generate", "a cat", "-o", str(out), "--num-frames", "9"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "[OK]" in err
    assert "run_id=" in err
    assert out.exists()


def test_cli_generate_records_state_on_failure(capsys, monkeypatch, tmp_path):
    """生成失败（disabled 路径）也写 state。"""
    state_path = tmp_path / ".state.json"
    rc = cli_main(["generate", "", "-o", str(tmp_path / "out.mp4"), "--state-path", str(state_path)])
    assert rc == 1
    s = state_mod.load_state(state_path)
    assert s.last_status == "failed"


def test_cli_generate_records_state_on_success(monkeypatch, tmp_path, capsys):
    _enable_overrides(tmp_path)
    state_path = tmp_path / ".state.json"
    out = tmp_path / "out.mp4"
    rc = cli_main(["generate", "a cat", "-o", str(out), "--state-path", str(state_path)])
    assert rc == 0
    s = state_mod.load_state(state_path)
    assert s.last_status == "succeeded"
    assert s.last_prompt == "a cat"
    assert s.last_num_frames == local_client.DEFAULT_NUM_FRAMES


def test_cli_status_no_state_returns_2(monkeypatch, tmp_path, capsys):
    state_path = tmp_path / ".state.json"
    rc = cli_main(["status", "--state-path", str(state_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "无 last_run_id" in err


def test_cli_status_reads_state(monkeypatch, tmp_path, capsys):
    state_path = tmp_path / ".state.json"
    state_mod.update_last(
        run_id="r_xyz", prompt="hi", output_path="/tmp/x.mp4",
        status="succeeded", model="THUDM/CogVideoX-2b", device="cpu",
        num_frames=49, duration_seconds=1.0, path=state_path,
    )
    rc = cli_main(["status", "--state-path", str(state_path)])
    err = capsys.readouterr().err
    assert rc == 0
    assert "[STATUS]" in err
    assert "r_xyz" in err
    assert "succeeded" in err


def test_cli_log_no_records(tmp_path, capsys):
    rt = tmp_path
    rc = cli_main(["log", "--runtime-dir", str(rt)])
    err = capsys.readouterr().err
    assert rc == 0
    assert "暂无记录" in err


def test_cli_log_with_records(tmp_path, capsys):
    import json
    rt = tmp_path
    (rt / "runs.jsonl").write_text(
        json.dumps({"ts": "t1", "op": "inference", "ok": True, "source": "ok"}) + "\n"
        + json.dumps({"ts": "t2", "op": "inference", "ok": True, "source": "ok"}) + "\n",
        encoding="utf-8",
    )
    rc = cli_main(["log", "--runtime-dir", str(rt)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inference" in out
    assert out.count("✓") == 2


def test_cli_log_op_filter(tmp_path, capsys):
    import json
    rt = tmp_path
    (rt / "runs.jsonl").write_text(
        json.dumps({"ts": "t1", "op": "inference", "ok": True, "source": "ok"}) + "\n"
        + json.dumps({"ts": "t2", "op": "poll", "ok": True, "source": "ok"}) + "\n",
        encoding="utf-8",
    )
    rc = cli_main(["log", "--op", "inference", "--runtime-dir", str(rt)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inference" in out
    assert "poll" not in out


def test_cli_doctor_runs(monkeypatch, capsys):
    monkeypatch.setattr(local_client, "torch_available", lambda: True)
    monkeypatch.setattr(local_client, "diffusers_available", lambda: True)
    rc = cli_main(["doctor"])
    combined = capsys.readouterr()
    assert rc in (0, 1, 2)
    assert "[DOCTOR]" in combined.out + combined.err


# ---------- batch 加载 ----------

def test_load_prompts_basic(tmp_path):
    p = tmp_path / "prompts.txt"
    p.write_text("a cat on the moon\nocean waves at sunset\n", encoding="utf-8")
    assert load_prompts(p) == ["a cat on the moon", "ocean waves at sunset"]


def test_load_prompts_strips_comments_and_blank(tmp_path):
    p = tmp_path / "prompts.txt"
    p.write_text(
        "# 头部注释\n"
        "\n"
        "  a cat on the moon  \n"
        "# 中间注释\n"
        "\n"
        "ocean waves\n"
        "   \n",
        encoding="utf-8",
    )
    assert load_prompts(p) == ["a cat on the moon", "ocean waves"]


def test_load_prompts_raises_on_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompts(tmp_path / "nope.txt")


# ---------- batch 编排 ----------

def _fake_generate_ok_or_fail(monkeypatch, prompts_to_fail: set[int]):
    """返回把指定 index 标失败的 fake _client_generate。同时启用 local_client 覆盖，
    使 cmd_batch / cmd_generate 通过 env 检查。"""
    _enable_overrides_for_batch()
    from hailuo_video import batch as batch_mod

    def fake(prompt, output_path, **kwargs):
        from hailuo_video.local_client import VideoResult
        idx = int(Path(output_path).name.split("_", 1)[0])
        if idx in prompts_to_fail:
            return VideoResult(
                output_path=Path(output_path),
                ok=False,
                source="error",
                error=f"simulated failure for idx={idx}",
            )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"fake mp4")
        return VideoResult(
            output_path=Path(output_path),
            ok=True,
            source="ok",
            run_id=f"r_{idx}",
            model=local_client.DEFAULT_MODEL,
            device=local_client.DEFAULT_DEVICE,
            num_frames=49,
            duration_seconds=0.1,
            bytes=8,
        )

    monkeypatch.setattr(batch_mod, "_client_generate", fake)


def _enable_overrides_for_batch():
    """仅启用 local_client 的 _override_*，不影响 monkeypatch.delenv 的 fixture 隔离。"""
    sentinel = object()

    def fake_load(model, device, dtype, cache_dir):
        return sentinel

    def fake_call(pipeline, prompt, num_frames, height, width, num_steps, guidance_scale, seed, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\x00\x01\x02 fake mp4")

    local_client.set_overrides(load=fake_load, call=fake_call)


def test_run_batch_sequential_success(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, set())
    summary = run_batch(["p1", "p2", "p3"], tmp_path / "out")
    assert summary.total == 3
    assert summary.succeeded == 3
    assert summary.failed == 0
    assert summary.exit_code() == 0
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files[0].startswith("0000_")
    assert files[1].startswith("0001_")
    assert files[2].startswith("0002_")


def test_run_batch_idempotent_by_prompt(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, set())
    out = tmp_path / "out"
    s1 = run_batch(["same prompt"], out)
    s2 = run_batch(["same prompt"], out)
    assert s1.succeeded == 1 and s1.skipped == 0
    assert s2.succeeded == 0 and s2.skipped == 1


def test_run_batch_continues_on_failure_by_default(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, {1})
    summary = run_batch(["a", "b", "c"], tmp_path / "out")
    assert summary.total == 3
    assert summary.succeeded == 2
    assert summary.failed == 1
    assert summary.exit_code() == 1
    assert len(summary.failures) == 1
    assert "simulated failure" in summary.failures[0][2]


def test_run_batch_stop_on_failure(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, {1, 2})
    summary = run_batch(
        ["a", "b", "c", "d"], tmp_path / "out", stop_on_failure=True,
    )
    assert summary.total == 2
    assert summary.succeeded == 1
    assert summary.failed == 1


def test_run_batch_start_from_and_count(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, set())
    summary = run_batch(
        ["a", "b", "c", "d", "e"], tmp_path / "out",
        start_from=1, count=2,
    )
    assert summary.total == 2
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert any(f.startswith("0001_") for f in files)
    assert any(f.startswith("0002_") for f in files)
    assert not any(f.startswith("0000_") for f in files)


def test_run_batch_empty_prompts_raises(tmp_path):
    with pytest.raises(ValueError):
        run_batch([], tmp_path / "out")


def test_run_batch_start_beyond_end_raises(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        run_batch(["a", "b"], tmp_path / "out", start_from=5)


def test_run_batch_passes_through_kwargs(monkeypatch, tmp_path):
    """验证 batch 把参数透传给 _client_generate。"""
    from hailuo_video import batch as batch_mod
    seen_kwargs: dict = {}

    def fake(prompt, output_path, **kwargs):
        seen_kwargs.update(kwargs)
        from hailuo_video.local_client import VideoResult
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"x")
        return VideoResult(output_path=Path(output_path), ok=True, source="ok", bytes=1)

    monkeypatch.setattr(batch_mod, "_client_generate", fake)
    run_batch(["a"], tmp_path / "out", num_frames=9, steps=5, seed=42, guidance=7.5)
    assert seen_kwargs["num_frames"] == 9
    assert seen_kwargs["num_steps"] == 5
    assert seen_kwargs["seed_value"] == 42
    assert seen_kwargs["guidance_scale"] == 7.5


# ---------- batch CLI ----------

def test_cli_batch_fails_when_unready(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(local_client, "torch_available", lambda: False)
    monkeypatch.setattr(local_client, "diffusers_available", lambda: False)
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("a\n", encoding="utf-8")
    rc = cli_main(["batch", str(prompts), "-o", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "环境未就绪" in err


def test_cli_batch_missing_prompts_file(capsys, tmp_path):
    rc = cli_main(["batch", str(tmp_path / "nope.txt"), "-o", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "不存在" in err


def test_cli_batch_empty_after_strip(capsys, tmp_path):
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("# 纯注释\n\n   \n", encoding="utf-8")
    rc = cli_main(["batch", str(prompts), "-o", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "没解析到任何 prompt" in err


def test_cli_batch_success(monkeypatch, tmp_path, capsys):
    _fake_generate_ok_or_fail(monkeypatch, set())
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("a\nb\n", encoding="utf-8")
    rc = cli_main(["batch", str(prompts), "-o", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert rc == 0
    assert "成功 2" in err


def test_cli_batch_reports_failures(monkeypatch, tmp_path, capsys):
    _fake_generate_ok_or_fail(monkeypatch, {0})
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("a\nb\n", encoding="utf-8")
    rc = cli_main(["batch", str(prompts), "-o", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "失败 1" in err
    assert "失败清单" in err


def test_cli_batch_skip_existing_by_default(monkeypatch, tmp_path, capsys):
    _fake_generate_ok_or_fail(monkeypatch, set())
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("same\n", encoding="utf-8")
    out = tmp_path / "out"

    rc1 = cli_main(["batch", str(prompts), "-o", str(out)])
    assert rc1 == 0

    rc2 = cli_main(["batch", str(prompts), "-o", str(out)])
    err = capsys.readouterr().err
    assert rc2 == 0
    assert "跳过 1" in err


# ---------- manifest ----------

def test_manifest_writes_csv(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, {1})
    summary = run_batch(["a", "b", "c"], tmp_path / "out")
    csv_path = tmp_path / "manifest.csv"
    write_manifest(summary, csv_path)
    assert csv_path.exists()
    content = csv_path.read_text(encoding="utf-8")
    assert content.splitlines()[0] == ",".join(MANIFEST_HEADER)
    assert len(content.splitlines()) == 4  # header + 3 rows


def test_manifest_includes_all_columns(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, set())
    summary = run_batch(["a cat"], tmp_path / "out")
    csv_path = tmp_path / "manifest.csv"
    write_manifest(summary, csv_path)
    import csv as csv_mod
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv_mod.DictReader(fh))
    assert len(rows) == 1
    row = rows[0]
    assert row["index"] == "0"
    assert row["prompt"] == "a cat"
    assert row["status"] == "succeeded"
    assert row["source"] == "ok"
    assert int(row["bytes"]) == 8
    assert row["model"] == local_client.DEFAULT_MODEL
    assert row["device"] == local_client.DEFAULT_DEVICE
    assert int(row["num_frames"]) == 49
    assert row["error"] == ""


def test_manifest_marks_skipped(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, set())
    out = tmp_path / "out"
    run_batch(["p1"], out)  # 第一次跑
    s2 = run_batch(["p1"], out)  # 第二次 skip
    assert s2.skipped == 1

    csv_path = tmp_path / "manifest.csv"
    write_manifest(s2, csv_path)
    import csv as csv_mod
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv_mod.DictReader(fh))
    assert rows[0]["status"] == "skipped"
    assert rows[0]["source"] == ""
    assert rows[0]["model"] == ""


def test_manifest_marks_failure(monkeypatch, tmp_path):
    _fake_generate_ok_or_fail(monkeypatch, {0})
    summary = run_batch(["bad", "good"], tmp_path / "out")
    csv_path = tmp_path / "manifest.csv"
    write_manifest(summary, csv_path)
    import csv as csv_mod
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv_mod.DictReader(fh))
    assert rows[0]["status"] == "failed"
    assert "simulated failure" in rows[0]["error"]
    assert rows[1]["status"] == "succeeded"


def test_cli_batch_with_manifest_writes_file(monkeypatch, tmp_path, capsys):
    _fake_generate_ok_or_fail(monkeypatch, set())
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("a\nb\n", encoding="utf-8")
    manifest_path = tmp_path / "out_manifest.csv"
    rc = cli_main(["batch", str(prompts), "-o", str(tmp_path / "out"), "--manifest", str(manifest_path)])
    err = capsys.readouterr().err
    assert rc == 0
    assert manifest_path.exists()
    assert f"manifest -> {manifest_path}" in err
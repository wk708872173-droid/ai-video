"""V3.8 Batch 5 — W4 提示词知识库 + 检索增强 + 负提示词。

锁住两个已确认缺陷：

  缺陷 6  **零** negative prompt 支持：`image_engine` 请求体只有
          model / prompt / aspect_ratio / response_format / n / subject_references /
          prompt_optimizer —— 生成路径里没有任何"禁止出现什么"的表达。
  缺陷 9  **无** `knowledge/motion_params.json`（REQ-406 未做）——
          各 sample/*/README.md 里沉淀的大量实测经验只是散文，无法被程序消费。

文献依据：arXiv:2610.15098（TIDE：单次 LLM 调用产出正/负提示词，适配 3 quota/天）、
arXiv:2504.11739 + 2510.20206（RAPO/RAPO++：检索增强 + relation graph）、
arXiv:2608.17468（SAGE：知识路由）。

R4：所有测试都不打真 API；不写仓库内的知识库文件（用 tmp_path）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from hailuo_video import image_engine as ie  # noqa: E402
from hailuo_video import negative_prompt as np  # noqa: E402
from hailuo_video.knowledge_store import (  # noqa: E402
    MotionParamsStore,
    MotionParamRecord,
    record_result,
)


# ============================================================
# 负提示词规格
# ============================================================

def test_prohibited_content_is_non_empty_and_covers_style_drift():
    """必须含防漂移守卫词（与 ADR-006 同款）。"""
    items = " ".join(np.PROHIBITED_CONTENT).lower()
    assert "illustration" in items
    assert "cartoon" in items
    assert "anime" in items
    assert "cgi" in items


def test_build_negative_prompt_is_non_empty():
    spec = np.build_negative_prompt()
    assert not spec.is_empty()
    assert len(spec.items) >= 5


def test_build_negative_prompt_renders_inline_clause():
    """内联渲染（与 ADR-006 守卫词同款写法）。"""
    text = np.build_negative_prompt().render()
    assert text.startswith("no ")
    assert ", no " in text


def test_negative_prompt_includes_motion_items_for_dynamic_shot():
    class _Shot:
        motion_complexity = "simple"
    spec = np.build_negative_prompt(_Shot())
    assert "frozen frame" in " ".join(spec.items)
    assert spec.source == "base+motion"


def test_negative_prompt_includes_character_items_when_asked():
    spec = np.build_negative_prompt(include_character=True)
    assert "changing fur color" in " ".join(spec.items)


def test_negative_prompt_dedupes_and_limits():
    """追加项与基础集重复时要去重；总数受 limit 约束。"""
    spec = np.build_negative_prompt(extra=("illustration", "watermark"), limit=5)
    assert len(spec.items) <= 5
    assert len(set(spec.items)) == len(spec.items), "重复项必须去重"
    assert "illustration" in spec.items


def test_negative_prompt_empty_render():
    spec = np.NegativePromptSpec()
    assert spec.render() == ""
    assert spec.is_empty()


# ============================================================
# 后端能力声明 + 内联
# ============================================================

def test_backends_declare_no_native_negative_prompt():
    """[UNVERIFIED] image-01 / Gemini 都无独立字段 → 必须默认 False（不发未知字段）。"""
    assert ie.ImageBackend.supports_native_negative_prompt is False
    assert ie.MiniMaxImageBackend.supports_native_negative_prompt is False
    assert ie.GeminiImageBackend.supports_native_negative_prompt is False


def test_minimax_inlines_negative_prompt_into_prompt():
    """后端不支持独立字段 → 负提示词内联到 prompt 末尾（不发未知字段）。"""
    captured: dict = {}

    def fake_post(url, body, headers):
        captured["body"] = body
        return {"data": {"image_base64": ["eA=="]}}

    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        base_url_value="https://api.minimaxi.com/v1",
        model_value="image-01",
        response_format_value="base64",
        http_post=fake_post,
    )
    backend.generate("a cat", negative_prompt="no text, no watermark")
    body = captured["body"]
    assert "negative_prompt" not in body, "不能发未确认的字段（会 4xx 且已计费）"
    assert "no text" in body["prompt"]
    assert body["prompt"].startswith("a cat")


def test_minimax_sends_native_field_when_backend_supports_it():
    """能力声明为 True → 才发独立字段。"""
    captured: dict = {}

    def fake_post(url, body, headers):
        captured["body"] = body
        return {"data": {"image_base64": ["eA=="]}}

    backend = ie.MiniMaxImageBackend(
        api_key_value="x" * 20,
        base_url_value="https://api.minimaxi.com/v1",
        model_value="image-01",
        response_format_value="base64",
        http_post=fake_post,
    )
    backend.supports_native_negative_prompt = True
    backend.generate("a cat", negative_prompt="no text")
    assert captured["body"]["negative_prompt"] == "no text"
    assert captured["body"]["prompt"] == "a cat"


def test_gemini_inlines_negative_prompt():
    captured: dict = {}

    def fake_post(url, body, headers):
        captured["body"] = body
        return {"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "image/png", "data": "eA=="}}]}}]}

    backend = ie.GeminiImageBackend(
        api_key_value="AIza-test-key",
        base_url_value="https://generativelanguage.googleapis.com/v1beta",
        model_value="gemini-2.5-flash-image",
        http_post=fake_post,
    )
    backend.generate("a cat", negative_prompt="no text")
    text = captured["body"]["contents"][0]["parts"][-1]["text"]
    assert "no text" in text
    assert "negative_prompt" not in captured["body"]


def test_mock_backend_records_negative_prompt():
    backend = ie.MockImageBackend()
    results = backend.generate("a cat", negative_prompt="no text")
    assert backend.calls[0]["negative_prompt"] == "no text"
    # mock 与真后端行为一致：负提示词内联进 prompt（体现在返回结果上）
    assert "no text" in results[0].prompt
    assert backend.calls[0]["prompt"] == "a cat"


def test_agent_passes_negative_prompt_to_backend():
    from hailuo_video.agents import ImageGenAgent

    backend = ie.MockImageBackend()
    agent = ImageGenAgent()
    result = agent.execute({
        "prompt": "a cat",
        "image_backend": backend,
        "negative_prompt": "no text, no watermark",
        "intermediate_dir": None,
    })
    assert result.ok, result.error
    assert backend.calls[0]["negative_prompt"] == "no text, no watermark"


def test_orchestrator_builds_negative_prompt(tmp_path):
    """orchestrator 的 image_gen task 必须带负提示词。"""
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    sb = Storyboard(
        title="t",
        characters=[],
        scenes=[SceneSpec(id=1, location="x")],
        shots=[Shot(index=1, scene_id=1, shot_type="中景", description="d",
                    characters=["农夫"])],
    )
    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": 1, "intermediate_dir": tmp_path}
    task = agent._build_task("image_gen", 0, ctx, None)
    assert task["negative_prompt"]
    assert "no " in task["negative_prompt"]


# ============================================================
# 知识库
# ============================================================

def test_store_roundtrip(tmp_path):
    path = tmp_path / "kb.json"
    store = MotionParamsStore(path)
    store.add(MotionParamRecord(id="mp_1", camera_motion="缓慢推近",
                                lesson="推近要写进 prompt 前缀", tags=["推近"]))
    store.save()

    reloaded = MotionParamsStore(path)
    reloaded.load()
    assert len(reloaded) == 1
    assert reloaded.records[0].camera_motion == "缓慢推近"
    assert reloaded.records[0].lesson == "推近要写进 prompt 前缀"


def test_store_missing_file_is_empty_not_error(tmp_path):
    store = MotionParamsStore(tmp_path / "nope.json")
    store.load()
    assert len(store) == 0


def test_store_corrupt_file_is_empty_not_error(tmp_path):
    path = tmp_path / "kb.json"
    path.write_text("{not json", encoding="utf-8")
    store = MotionParamsStore(path)
    store.load()
    assert len(store) == 0


def test_search_finds_by_keyword(tmp_path):
    store = MotionParamsStore(tmp_path / "kb.json")
    store.add(MotionParamRecord(id="a", camera_motion="缓慢推近", lesson="L1",
                                tags=["推近", "转头"], subject_action="缓慢转头"))
    store.add(MotionParamRecord(id="b", camera_motion="拉远", lesson="L2",
                                tags=["远景"], subject_action="奔跑"))
    hits = store.search("推近")
    assert hits
    assert hits[0].id == "a"


def test_search_finds_by_chinese_bigram(tmp_path):
    """CJK 用字符二元组（无分词器依赖）。"""
    store = MotionParamsStore(tmp_path / "kb.json")
    store.add(MotionParamRecord(id="a", lesson="色锁写在描述末尾会失效"))
    store.add(MotionParamRecord(id="b", lesson="跨镜色差需要门禁"))
    hits = store.search("色锁")
    assert hits and hits[0].id == "a"


def test_search_empty_query_returns_nothing(tmp_path):
    store = MotionParamsStore(tmp_path / "kb.json")
    store.add(MotionParamRecord(id="a", lesson="x"))
    assert store.search("") == []


def test_search_prefers_ok_over_failed(tmp_path):
    """outcome=ok 的经验优先（failed 的轻微降权）。"""
    store = MotionParamsStore(tmp_path / "kb.json")
    store.add(MotionParamRecord(id="ok1", lesson="推近", outcome="ok"))
    store.add(MotionParamRecord(id="bad1", lesson="推近", outcome="failed"))
    hits = store.search("推近")
    assert hits[0].id == "ok1"


def test_related_follows_relation_graph(tmp_path):
    store = MotionParamsStore(tmp_path / "kb.json")
    store.add(MotionParamRecord(id="a", lesson="A"))
    store.add(MotionParamRecord(id="b", lesson="B"))
    store.relate("a", "b", "same_scene", weight=0.9)
    near = store.related("a")
    assert [r.id for r in near] == ["b"]
    assert [r.id for r in store.related("b")] == ["a"]


def test_lessons_returns_one_liners(tmp_path):
    store = MotionParamsStore(tmp_path / "kb.json")
    store.add(MotionParamRecord(id="a", lesson="经验一"))
    store.add(MotionParamRecord(id="b", lesson="经验二"))
    lessons = store.lessons("经验")
    assert lessons
    assert all(isinstance(x, str) and x for x in lessons)


def test_record_result_appends_and_saves(tmp_path):
    path = tmp_path / "kb.json"
    store = MotionParamsStore(path)

    class _Shot:
        shot_id = "shot_001"
        camera_motion = "缓慢推近"
        motion_complexity = "simple"
        expected_duration = 6.0
        subject_action = "转头"
        color_lock = "warm-gold"

    rec = record_result(store, shot=_Shot(), prompt_positive="p",
                        prompt_negative="n", quality={"color_sim_mean": 0.9},
                        outcome="ok", lesson="L", tags=["t"], save=True)
    assert rec.id
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["records"]) == 1
    assert data["records"][0]["lesson"] == "L"


def test_record_result_merges_into_existing(tmp_path):
    path = tmp_path / "kb.json"
    store = MotionParamsStore(path)
    store.add(MotionParamRecord(id="seed", lesson="种子"))
    store.save()

    store2 = MotionParamsStore(path)
    store2.load()
    store2.add(MotionParamRecord(lesson="新增"))
    store2.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["records"]) == 2


# ---------- 仓库里的种子库 ----------

def test_repo_knowledge_file_exists_and_is_valid():
    """REQ-406：knowledge/motion_params.json 必须存在且结构合法。"""
    path = Path(__file__).resolve().parents[1] / "knowledge" / "motion_params.json"
    assert path.is_file(), "缺少 knowledge/motion_params.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["_schema_version"] == "1.0"
    assert len(data["records"]) >= 5
    for rec in data["records"]:
        assert "id" in rec and "lesson" in rec
        assert rec["lesson"], f"记录 {rec.get('id')} 没有经验文本"
    assert data["relations"], "relation graph 不能为空（RAPO 的检索依据）"


def test_repo_knowledge_has_no_empty_records():
    """种子库不能混入测试产生的空记录。"""
    path = Path(__file__).resolve().parents[1] / "knowledge" / "motion_params.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    empty = [r.get("id") for r in data["records"] if not r.get("lesson")]
    assert not empty, f"知识库混入空记录：{empty}"


def test_repo_knowledge_is_searchable():
    """种子库能被检索命中（证明种子内容真的可用）。"""
    path = Path(__file__).resolve().parents[1] / "knowledge" / "motion_params.json"
    store = MotionParamsStore(path)
    hits = store.search("色锁 多主体")
    assert hits, "种子库检索不到任何记录"


# ============================================================
# 默认关闭：知识库写库是 opt-in
# ============================================================

def test_knowledge_recording_is_off_by_default(tmp_path):
    """默认不写知识库（写库是副作用，不能污染人工维护的种子数据）。"""
    from hailuo_video.orchestrator import PipelineConfig

    assert PipelineConfig().knowledge_path is None


def test_cli_pipeline_exposes_knowledge_path():
    from hailuo_video import cli

    parser = cli.build_parser()
    args = parser.parse_args([
        "pipeline", "sb.json", "-o", "out.mp4",
        "--knowledge-path", "knowledge/motion_params.json",
    ])
    assert args.knowledge_path == "knowledge/motion_params.json"
    args2 = parser.parse_args(["pipeline", "sb.json", "-o", "out.mp4"])
    assert args2.knowledge_path is None

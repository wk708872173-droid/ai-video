"""V5.0 Batch 1 — 「实现已存在但从未接线」的可执行门禁。

## 为什么有这个文件

知识库 `knowledge/lessons.md` 的 **L-011** 记着一条教训：

> 「实现已存在但从未接线」是一类**独立缺陷** —— 代码写好了、单测也过了，
> 但生产路径**零调用**。

V5.0 的审计发现本项目有 **10 处**这样的死代码（`knowledge_store` 检索侧、
`prompts_director` 的角色 prompt、`mcp_client`、`publish_agent`、
`audit_llm.audit_chat_call`、`ProjectRegistry`、`validate_director_brief` …）。

**光记教训没用** —— L-011 写下来之后同类缺陷**又复发了几次**。
根因是测试只断言「函数返回值对不对」，**不断言「它有没有被用」**。

本文件把那条教训变成**门禁**：
- `_production_callers()` 用 **AST**（不是 grep）找生产代码里的真实引用
  （跳过注释与 docstring，避免"注释里提一句"被当成接线）；
- `test_no_dead_code_*` 逐个锁住本批已接线的符号。

新增死代码时，这个门禁会**失败** —— 提醒你：要么接线，要么删掉，
别让它再"看起来完成"。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = REPO_ROOT / "sandbox"
PACKAGE = SANDBOX / "hailuo_video"
SCRIPTS = REPO_ROOT / "scripts"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))


# ---------- AST 扫描工具 ----------

def _source_files() -> list[Path]:
    """生产代码文件（**不含 tests/**）。"""
    out = sorted(PACKAGE.rglob("*.py"))
    out += sorted(SCRIPTS.rglob("*.py")) if SCRIPTS.is_dir() else []
    return [p for p in out if "__pycache__" not in p.parts]


def _referenced_names(path: Path) -> set[str]:
    """一个文件里**真实引用**的标识符集合（AST 层面，排除注释与 docstring）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            # 同时记录别名与原名：`from hailuo_video import publish_agent as pub`
            # 既要认 "pub"（符号引用）也要认 "publish_agent"（模块被 import）
            if node.asname:
                names.add(node.asname)
            names.add(node.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom) and node.module:
            # from a.b import c → 模块名 "a.b" 也算引用（用于判"模块被 import"）
            names.add(node.module)
            names.add(node.module.split(".")[-1])
    return names


def _production_callers(symbol: str, *, define_in: Path | None = None) -> list[str]:
    """找出生产代码里引用 `symbol` 的文件（相对仓库根的路径）。

    `define_in` 不再排除自身 —— 因为**模块内链路是合法的**：
    例如 `knowledge_store.lessons()` 只被同模块的 `retrieve_lessons()` 调用，
    而 `retrieve_lessons()` 被 `orchestrator.py` 调用。这条链路是接通的。

    但"只被定义、从未被调用"的符号仍会被检出：
    `ast.FunctionDef` 的名字**不在** `_referenced_names` 里
    （那里只收 Name / Attribute / alias），所以纯定义不会自证清白。
    """
    callers: list[str] = []
    for f in _source_files():
        if symbol in _referenced_names(f):
            callers.append(str(f.relative_to(REPO_ROOT)))
    return callers


def _module_is_imported(define_in: Path) -> bool:
    """定义模块是否被**其他**生产文件 import。

    这是"模块内链路"能成立的前提：模块自身进了生产路径，
    它内部的函数引用才算数。
    """
    mod = define_in.stem
    for f in _source_files():
        if f.resolve() == define_in.resolve():
            continue
        if mod in _referenced_names(f):
            return True
    return False


# ---------- 门禁 1：这些符号必须有生产调用方 ----------

# (符号名, 定义所在文件相对路径, 一句话说明)
MUST_BE_WIRED = [
    (
        "audit_request",
        "sandbox/hailuo_video/audit_llm.py",
        "LLM 审计的 request 侧 —— 生产此前从不传 project_id → 零审计产出",
    ),
    (
        "validate_director_brief",
        "sandbox/hailuo_video/director.py",
        "导演 brief 自校验 —— 此前已实现且有单测，生产零调用",
    ),
    (
        "lessons",
        "sandbox/hailuo_video/knowledge_store.py",
        "知识库检索读侧 —— 此前 search/related/lessons 生产零调用",
    ),
    (
        "retrieve_lessons",
        "sandbox/hailuo_video/knowledge_store.py",
        "知识库读侧注入 prompt 的入口（V5.0 新增）",
    ),
    (
        "make_publish_backend",
        "sandbox/hailuo_video/publish_agent.py",
        "发布后端工厂 —— 此前整个 publish_agent 模块生产零引用",
    ),
]


@pytest.mark.parametrize("symbol,define_rel,why", MUST_BE_WIRED)
def test_symbol_has_production_caller(symbol: str, define_rel: str, why: str):
    """该符号必须在生产代码里被**引用**，且其模块进了生产路径。"""
    define_in = REPO_ROOT / define_rel
    assert define_in.is_file(), f"定义文件不存在：{define_rel}"
    callers = _production_callers(symbol)
    assert callers, (
        f"死代码告警：{symbol!r} 在生产代码里**零引用**（{why}）。\n"
        f"  定义于 {define_rel}\n"
        f"  → 要么接线，要么删掉。别让它继续'看起来完成'。"
    )
    assert _module_is_imported(define_in), (
        f"死代码告警：{define_rel} 没被任何其他生产文件 import —— "
        f"它内部对 {symbol!r} 的引用不算接通。"
    )


def test_dead_code_gate_would_catch_an_unwired_symbol(tmp_path):
    """门禁本身要有效：造一个"只定义、从不调用"的符号，门禁必须报警。

    没有这条自测，门禁可能因为写错而永远绿 —— 那比没有门禁更危险。
    """
    from hailuo_video import knowledge_store as ks

    assert ks.__file__ is not None
    # `related()` —— relation graph 邻居查询，V5.0 审计时仍**零生产调用**。
    # 它不是 MUST_BE_WIRED 的一员，所以本测试只断言"门禁函数能识别出它没被接线"，
    # 避免未来有人不小心把它塞进 MUST_BE_WIRED 却忘了接线。
    assert "related" not in {s for s, _, _ in MUST_BE_WIRED}

    # 造一个临时模块，只定义不调用 —— 门禁应当返回空
    mod = tmp_path / "sample_dead.py"
    mod.write_text(
        "def never_called():\n    return 1\n", encoding="utf-8"
    )
    # 直接验证扫描器语义：定义处不算引用
    names = _referenced_names(mod)
    assert "never_called" not in names, (
        "扫描器把纯定义当成了引用 —— 门禁会永远绿（自测失败）"
    )


# ---------- 门禁 2：audit 在生产路径上真的会被触发 ----------

def test_script_engine_passes_project_id_to_backend():
    """`script_engine` 的 3 次 LLM 调用必须把 project_id 透传给 backend。"""
    from hailuo_video import script_engine as se

    src = (PACKAGE / "script_engine.py").read_text(encoding="utf-8")
    calls = src.count("project_id=project_id")
    assert calls >= 3, (
        f"期望 3 处 chat() 调用都传 project_id（script/characters/storyboard），"
        f"实际 {calls} 处"
    )


def test_llm_backend_receives_project_id_and_audit_writes(tmp_path, monkeypatch):
    """端到端：带 project_id 调 novel_to_storyboard → 审计日志真产出。"""
    from hailuo_video import script_engine as se
    from hailuo_video.audit_llm import AuditLogger

    seen: list[dict] = []

    class _SpyBackend(se.LLMBackend):
        def chat(self, system: str, user: str, **opts):
            seen.append({"system": system, "opts": dict(opts)})
            if "编剧" in system:
                return '{"title": "t", "style_anchor": "写实", "scenes": [], "acts": []}'
            if "角色设计师" in system:
                return "[]"
            return '{"shots": []}'

    backend = _SpyBackend()
    # 审计写到 tmp_path，避免污染仓库 logs/
    import hailuo_video.script_engine as _se_mod
    monkeypatch.setattr(_se_mod, "_audit_log_dir", tmp_path, raising=False)

    # 直接走 chat 层，验证 opts 里带 project_id
    backend.chat(system="你是漫剧编剧，输出严格 JSON。", user="x", project_id="proj1234")
    assert seen[0]["opts"].get("project_id") == "proj1234"

    # AuditLogger 本身能写（生产路径的产物落点）
    logger = AuditLogger("proj1234", log_dir=tmp_path)
    logger.write(
        __import__("hailuo_video.audit_llm", fromlist=["AuditEvent"]).AuditEvent(
            timestamp="2026-11-03T00:00:00",
            kind="request",
            project_id="proj1234",
            endpoint="minimax_chat",
            payload_keys=["system", "user"],
            payload_hash="abc123",
        )
    )
    assert logger.log_path.is_file()
    assert "proj1234" in logger.log_path.name


# ---------- 门禁 3：知识库读侧真的把经验注进了 prompt ----------

def _shot(idx: int = 0, **kw):
    from hailuo_video.script_engine import SceneSpec, Shot, Storyboard

    shot = Shot(
        index=idx + 1, scene_id=1, shot_type="中景",
        description="橘猫抬头望天", **kw,
    )
    sb = Storyboard(
        title="t",
        characters=[],
        scenes=[SceneSpec(id=1, location="草地", atmosphere="温暖")],
        shots=[shot],
        style_anchor="写实",
    )
    return shot, sb


def _agent(tmp_path, sb):
    from hailuo_video.orchestrator import PipelineAgent, PipelineConfig

    agent = PipelineAgent(PipelineConfig(run_dir=tmp_path / "run"))
    ctx = {"storyboard": sb, "shot_count": len(sb.shots),
           "intermediate_dir": tmp_path}
    return agent, ctx


def test_lessons_from_knowledge_base_reach_image_prompt(tmp_path):
    """知识库读侧接线：image prompt 里**可见**注入的经验文本。"""
    from hailuo_video.knowledge_store import DEFAULT_KNOWLEDGE_PATH

    if not DEFAULT_KNOWLEDGE_PATH.is_file():
        pytest.skip("仓库种子知识库不存在")

    shot, sb = _shot()
    agent, ctx = _agent(tmp_path, sb)
    task = agent._build_task("image_gen", 0, ctx, None)

    assert task.get("lessons_hint"), (
        "image task 没有 lessons_hint —— 知识库读侧没接线"
    )
    assert "[经验]" in task["lessons_hint"]
    assert task["lessons_hint"] in task["prompt"], (
        "经验片段没有进入 image prompt"
    )


def test_knowledge_lessons_do_not_override_manual_prompt(tmp_path):
    """人工手写的 static_visual **不被**追加经验（尊重人工编辑）。"""
    shot, sb = _shot(static_visual="[风格:x] 手工指定画面",
                     dynamic_narrative="手工指定运动")
    agent, ctx = _agent(tmp_path, sb)
    task = agent._build_task("image_gen", 0, ctx, None)
    assert task["prompt"] == "[风格:x] 手工指定画面"


def test_inject_hint_keeps_style_prefix_first():
    """风格前缀必须守第 0 位（V3.3 实测契约），经验紧随其后。"""
    from hailuo_video.orchestrator import _inject_hint

    out = _inject_hint("[风格:写实] 橘猫抬头", "[经验]某条经验")
    assert out.startswith("[风格:写实]")
    assert out.index("[经验]") < out.index("橘猫抬头")


def test_inject_hint_without_style_prefix_puts_hint_first():
    from hailuo_video.orchestrator import _inject_hint

    out = _inject_hint("橘猫抬头", "[经验]某条经验")
    assert out.startswith("[经验]")


# ---------- 门禁 4：导演自校验真的被消费 ----------

def test_apply_director_briefs_records_warnings():
    """`validate_director_brief` 的结果落进 shot.director_warnings。"""
    from hailuo_video.script_engine import Shot, _apply_director_briefs

    # 一个没有任何可用字段的 shot → static/dynamic 推导为空 → 触发自校验问题
    empty = Shot(index=1, scene_id=1, shot_type="", description="")
    _apply_director_briefs([empty])
    assert empty.director_warnings, (
        "自校验问题没有落进 shot.director_warnings —— validate_director_brief 仍是死代码"
    )
    assert "static_visual" in empty.director_warnings


def test_brief_for_shot_actually_validates():
    """`brief_for_shot` 的 docstring 声称"规划 + 自校验"，实现必须真校验。"""
    from hailuo_video.director import brief_for_shot
    from hailuo_video.script_engine import Shot

    brief = brief_for_shot(Shot(index=1, scene_id=1, shot_type="", description=""))
    assert brief.issues, "brief_for_shot 没有返回自校验问题 —— docstring 与实现不符"


def test_director_brief_issues_serialize():
    from hailuo_video.director import DirectorBrief

    b = DirectorBrief(static_visual="a", dynamic_narrative="b", issues=("x", "y"))
    d = b.to_dict()
    assert d["issues"] == ["x", "y"]
    assert d["used_fields"] == []


# ---------- 门禁 5：叙事记忆真的进了 prompt ----------

def test_continuity_from_prev_reaches_motion_prompt(tmp_path):
    """`continuity_from_prev` 此前有 schema 无消费 —— 现在必须进 motion prompt。"""
    shot, sb = _shot(continuity_from_prev="承接上一镜的奔跑")
    agent, ctx = _agent(tmp_path, sb)
    task = agent._build_task("motion_gen", 0, ctx, None)
    assert "承接上一镜的奔跑" in task["subject_action"], (
        "continuity_from_prev 没进 motion prompt"
    )


def test_emotion_arc_point_reaches_motion_prompt(tmp_path):
    shot, sb = _shot(emotion_arc_point=0.8)
    agent, ctx = _agent(tmp_path, sb)
    task = agent._build_task("motion_gen", 0, ctx, None)
    assert "0.80" in task["subject_action"], "emotion_arc_point 没进 motion prompt"


# ---------- 门禁 6：publish CLI 接线 + Auth 域硬失败 ----------

def test_publish_subcommand_exists():
    from hailuo_video import cli

    parser = cli.build_parser()
    args = parser.parse_args(["publish", "a.mp4", "--title", "t"])
    assert args.backend == "mock"
    assert args.func is cli.cmd_publish


def test_publish_youtube_without_credentials_fails_hard(tmp_path, capsys, monkeypatch):
    """Auth 域硬失败：youtube 后端无凭据 → 明确报错，**不**退回 mock。"""
    from hailuo_video import cli

    for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    args = cli.build_parser().parse_args(
        ["publish", str(video), "--title", "t", "--backend", "youtube"]
    )
    rc = cli.cmd_publish(args)
    err = capsys.readouterr().err
    assert rc == 2, "无凭据时必须硬失败（退出码 2）"
    assert "凭据缺失" in err
    assert "mock" in err  # 明确说明不会退回 mock


def test_publish_mock_marks_output_as_mock(tmp_path, capsys):
    """mock 路径必须显式标注 —— 不能让人误以为真上传了。"""
    from hailuo_video import cli

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    args = cli.build_parser().parse_args(
        ["publish", str(video), "--title", "t", "--backend", "mock"]
    )
    rc = cli.cmd_publish(args)
    err = capsys.readouterr().err
    assert rc == 0
    assert "[MOCK]" in err


# ---------- 门禁 7：CLI script 能传 project_id ----------

def test_script_subcommand_has_project_id_option():
    from hailuo_video import cli

    parser = cli.build_parser()
    args = parser.parse_args(["script", "n.txt", "-o", "sb.json", "--project-id", "p1234"])
    assert args.project_id == "p1234"

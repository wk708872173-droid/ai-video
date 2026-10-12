"""V2.5 Phase 0 - 物料准备：multi-angle character sheet + scene sheet。

根因：单 shot 独立生成无法保证角色一致性（MiniMax 文本→像素非确定性）。
解法：先为每个唯一角色生成多角度 character sheet（front/side/back），
每个 scene 生成 1 张 scene sheet；后续 shot 按 shot_type 自动选最匹配的角度，
作为 subject_references 列表传给 MiniMax（i2i 模式）。

工作流：
  1. python -m hailuo_video prep storyboard.json   # 只跑 Phase 0，缓存到 assets/
  2. python -m hailuo_video pipeline storyboard.json  # Phase 0 + 1 + 2，读缓存 + 跑 shot

缓存结构：
  assets/
  ├── characters/<safe_name>/
  │   ├── front.jpg  + front.url.txt     # 正面 reference
  │   ├── side.jpg   + side.url.txt      # 侧面 reference（V2.5）
  │   └── back.jpg   + back.url.txt      # 背面 reference（V2.5）
  └── scenes/
      ├── scene_0001.jpg  + scene_0001.url.txt
      └── ...

跳过缓存：删除对应 .jpg 即重新生成（--force 全量重生成）。
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from hailuo_video import image_engine as ie
from hailuo_video.script_engine import Character, SceneSpec, Storyboard


DEFAULT_ASSETS_DIR = Path("./assets")  # 相对 cwd；CLI 可改


# ---------- Prompt 模板 ----------

# character sheet: 4 角度（V2.5.2 多方向版本，pan_left/pan_right 智能选择）
CHARACTER_ANGLE_PROMPTS = {
    "front": (
        "character design reference sheet, front view, "
        "full body, neutral expression, simple light gray background, "
        "concept art style, {style_anchor}, {appearance}, color palette: {palette}"
    ),
    # V2.5.2：分两个 side 角度；MiniMax 看到"left side" / "right side" 关键词会出对应方向
    "side_left": (
        "character design reference sheet, three-quarter LEFT side view, "
        "showing the character's left side, left arm forward, "
        "full body, walking pose, simple light gray background, "
        "concept art style, {style_anchor}, {appearance}, color palette: {palette}"
    ),
    "side_right": (
        "character design reference sheet, three-quarter RIGHT side view, "
        "showing the character's right side, right arm forward, "
        "full body, walking pose, simple light gray background, "
        "concept art style, {style_anchor}, {appearance}, color palette: {palette}"
    ),
    "back": (
        "character design reference sheet, three-quarter back view, "
        "full body, looking away, simple light gray background, "
        "concept art style, {style_anchor}, {appearance}, color palette: {palette}"
    ),
    # 兼容 V2.5 的 generic side（pan 方向不确定时兜底；不再默认生成）
    "side": (
        "character design reference sheet, three-quarter side view, "
        "full body, walking pose, simple light gray background, "
        "concept art style, {style_anchor}, {appearance}, color palette: {palette}"
    ),
}

# 默认生成的 4 角度（不含老版 side；V2.5.2 起）
DEFAULT_ANGLES = ("front", "side_left", "side_right", "back")

# 兼容旧版（V3 单角度 front-only）
CHARACTER_SHEET_PROMPT = CHARACTER_ANGLE_PROMPTS["front"]


# V2.5.2：character 表情 sheet（3 种，组合 4 角度 = 12 sheets/char）
EXPRESSION_PROMPTS = {
    "neutral": (
        "character expression reference sheet, neutral calm face, "
        "slight natural smile, eyes forward, relaxed pose, "
        "simple light gray background, {style_anchor}, {appearance}, color palette: {palette}"
    ),
    "happy": (
        "character expression reference sheet, big happy smile, bright eyes, "
        "cheerful expression, eyebrows raised, "
        "simple light gray background, {style_anchor}, {appearance}, color palette: {palette}"
    ),
    "angry_sad": (
        "character expression reference sheet, distressed anguished face, "
        "furrowed eyebrows, downturned mouth, sad or alarmed eyes, "
        "simple light gray background, {style_anchor}, {appearance}, color palette: {palette}"
    ),
}

DEFAULT_EXPRESSIONS = ("neutral", "happy", "angry_sad")


# V2.5.2：scene 时间变体 sheet（白天/黄昏/夜）
SCENE_VARIANT_PROMPTS = {
    "day": (
        "scene environment reference sheet, wide establishing shot, "
        "BRIGHT DAYTIME atmosphere, golden morning sunlight, blue sky, "
        "{style_anchor}, location: {location}, "
        "key elements: {key_elements}, "
        "silhouettes of characters for scale, no detailed faces"
    ),
    "sunset": (
        "scene environment reference sheet, wide establishing shot, "
        "SUNSET DUSK atmosphere, orange-red sky, golden hour lighting, long shadows, "
        "{style_anchor}, location: {location}, "
        "key elements: {key_elements}, "
        "silhouettes of characters for scale, no detailed faces"
    ),
    "night": (
        "scene environment reference sheet, wide establishing shot, "
        "NIGHTIME atmosphere, dark blue sky, moonlight, stars visible, "
        "{style_anchor}, location: {location}, "
        "key elements: {key_elements}, "
        "silhouettes of characters for scale, no detailed faces"
    ),
}

DEFAULT_SCENE_VARIANTS = ("day", "sunset", "night")

# 兼容旧版的单变体 scene sheet prompt（V2.5 / V2.5.1）
SCENE_SHEET_PROMPT = SCENE_VARIANT_PROMPTS["day"]


# ---------- V3.4 ADR-003：style_anchor 防漂移守卫 ----------

# V3.3 暴露的 bug：story_arc style_anchor 是写实摄影系，但 asset_prep character sheet
# 仍输出橘色卡通玩具狗。原因：prompt 头硬编码 "concept art style" + 用户 style_anchor
# 同时存在 → 模型挑简单的 "concept art style" 实现 → 卡通。
# 修复：
#   - style_anchor 为空 → 强制兜底为写实系（不再是 "concept art style"）
#   - 已含守卫词 → 不重复追加
#   - 缺守卫词 → 末尾自动追加 "no illustration no cartoon no anime no CGI"
PHOTOREALISTIC_FALLBACK = (
    "photorealistic cinematic documentary, natural lighting, "
    "no illustration no cartoon no anime no CGI concept art"
)
GUARD_PHRASE = "no illustration no cartoon no anime no CGI"


def _safe_style_anchor(style_anchor: str | None) -> str:
    """V3.4 ADR-003：style_anchor 防漂移。

    - 空值 → 兜底为写实系（不再退回 "concept art style"）
    - 已含守卫词 → 不重复追加
    """
    anchor = (style_anchor or "").strip()
    if not anchor:
        return PHOTOREALISTIC_FALLBACK
    if any(kw in anchor.lower() for kw in ("no illustration", "no cartoon", "no anime", "no cgi")):
        return anchor
    return f"{anchor}, {GUARD_PHRASE}"


# ---------- 数据类 ----------

@dataclass
class AssetResult:
    name: str
    image_path: str                  # 本地路径（jpg）
    url: str = ""                    # 24h URL（subject_reference 用）
    url_expires_at: str = ""         # ISO 过期时间（粗估，不精确）
    source: str = "ok"               # "ok" | "cached" | "error"
    error: str = ""
    duration_seconds: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.source in ("ok", "cached")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- Asset 路径 ----------

def _safe_filename(name: str) -> str:
    """把任意字符串转成安全文件名（保留中文 + 数字 + 字母 + -_）。"""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return safe[:64] or "unnamed"


def character_dir(assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return Path(assets_dir) / "characters"


def character_subdir(name: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    """每角色独立子目录（V2.5 多角度布局）。"""
    return character_dir(assets_dir) / _safe_filename(name)


def character_angle_path(name: str, angle: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return character_subdir(name, assets_dir) / f"{angle}.jpg"


def character_angle_url_path(name: str, angle: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return character_subdir(name, assets_dir) / f"{angle}.url.txt"


def scene_dir(assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return Path(assets_dir) / "scenes"


def scene_path(scene_id: int, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return scene_dir(assets_dir) / f"scene_{scene_id:04d}.jpg"


def scene_url_path(scene_id: int, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return scene_dir(assets_dir) / f"scene_{scene_id:04d}.url.txt"


def scene_variant_path(scene_id: int, variant: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return scene_dir(assets_dir) / f"scene_{scene_id:04d}_{variant}.jpg"


def scene_variant_url_path(scene_id: int, variant: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return scene_dir(assets_dir) / f"scene_{scene_id:04d}_{variant}.url.txt"


# V2.5.2 新增：character 表情 sheet 路径
def character_expression_path(name: str, expression: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return character_subdir(name, assets_dir) / f"expression_{expression}.jpg"


def character_expression_url_path(name: str, expression: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> Path:
    return character_subdir(name, assets_dir) / f"expression_{expression}.url.txt"


def has_cached_character_angle(name: str, angle: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> bool:
    return (
        character_angle_path(name, angle, assets_dir).exists()
        and character_angle_url_path(name, angle, assets_dir).exists()
    )


def has_cached_character_expression(name: str, expression: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> bool:
    return (
        character_expression_path(name, expression, assets_dir).exists()
        and character_expression_url_path(name, expression, assets_dir).exists()
    )


def has_cached_character_all_angles(name: str, assets_dir: Path = DEFAULT_ASSETS_DIR, angles: tuple[str, ...] = DEFAULT_ANGLES) -> bool:
    return all(has_cached_character_angle(name, a, assets_dir) for a in angles)


def has_cached_scene(scene_id: int, assets_dir: Path = DEFAULT_ASSETS_DIR) -> bool:
    return scene_path(scene_id, assets_dir).exists() and scene_url_path(scene_id, assets_dir).exists()


def has_cached_scene_variant(scene_id: int, variant: str, assets_dir: Path = DEFAULT_ASSETS_DIR) -> bool:
    return scene_variant_path(scene_id, variant, assets_dir).exists() and scene_variant_url_path(scene_id, variant, assets_dir).exists()


def has_cached_scene_all_variants(scene_id: int, assets_dir: Path = DEFAULT_ASSETS_DIR, variants: tuple[str, ...] = DEFAULT_SCENE_VARIANTS) -> bool:
    return all(has_cached_scene_variant(scene_id, v, assets_dir) for v in variants)


# ---------- URL 过期时间提取 ----------

def _extract_url_expiry(url: str) -> str:
    """从 OSS 签名 URL 提取 Expires 参数（秒级时间戳 → ISO）。"""
    import datetime
    try:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        exp = int(qs.get("Expires", ["0"])[0])
        if exp > 0:
            return datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc).isoformat()
    except Exception:
        pass
    return ""


# ---------- 内部：单角度生成（无 angle 调度） ----------

def _generate_one_angle(
    name: str,
    angle: str,
    prompt: str,
    image_backend: ie.ImageBackend,
    assets_dir: Path,
    force_regenerate: bool,
    log: Callable[[str], None],
) -> AssetResult:
    img_path = character_angle_path(name, angle, assets_dir)
    url_path = character_angle_url_path(name, angle, assets_dir)

    if not force_regenerate and has_cached_character_angle(name, angle, assets_dir):
        cached_url = url_path.read_text(encoding="utf-8").strip()
        log(f"[asset-prep] cache hit: {name}/{angle}")
        return AssetResult(
            name=f"{name}/{angle}",
            image_path=str(img_path),
            url=cached_url,
            source="cached",
            extra={"path": str(img_path), "angle": angle},
        )

    started = time.monotonic()
    try:
        results = image_backend.generate(
            prompt,
            aspect_ratio_value="1:1",
            response_format_value="url",
        )
    except Exception as exc:
        return AssetResult(
            name=f"{name}/{angle}",
            image_path=str(img_path),
            source="error",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
            extra={"angle": angle},
        )
    elapsed = time.monotonic() - started

    r = results[0]
    if not r.image_url:
        return AssetResult(
            name=f"{name}/{angle}",
            image_path=str(img_path),
            source="error",
            error="response_format=url 但未拿到 image_url",
            duration_seconds=elapsed,
            extra={"angle": angle},
        )

    try:
        data = urllib.request.urlopen(r.image_url[0], timeout=30).read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return AssetResult(
            name=f"{name}/{angle}",
            image_path=str(img_path),
            source="error",
            error=f"下载 url 失败：{exc}",
            duration_seconds=elapsed,
            extra={"angle": angle},
        )

    img_path.write_bytes(data)
    url_path.write_text(r.image_url[0], encoding="utf-8")
    log(f"[asset-prep] generated: {name}/{angle} → {img_path} ({len(data)} bytes)")
    return AssetResult(
        name=f"{name}/{angle}",
        image_path=str(img_path),
        url=r.image_url[0],
        url_expires_at=_extract_url_expiry(r.image_url[0]),
        source="ok",
        duration_seconds=elapsed,
        extra={"bytes": len(data), "angle": angle},
    )


# ---------- 生成 character sheet（V2.5 多角度） ----------

def generate_character_sheet(
    character: Character,
    image_backend: ie.ImageBackend,
    *,
    style_anchor: str = "",
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
    angles: tuple[str, ...] = DEFAULT_ANGLES,    # V2.5：默认 3 角度
) -> dict[str, AssetResult]:
    """为单个角色生成多角度 character sheet。

    返回 {angle: AssetResult}。
    缓存布局：`assets/characters/<safe_name>/<angle>.jpg` + `.url.txt`
    """
    log = log_callback or (lambda msg: print(msg, flush=True))
    cd = character_subdir(character.name, assets_dir)
    cd.mkdir(parents=True, exist_ok=True)

    out: dict[str, AssetResult] = {}
    log(f"[asset-prep] preparing {len(angles)} angle(s) for {character.name}: {angles}")

    for angle in angles:
        prompt_template = CHARACTER_ANGLE_PROMPTS.get(angle)
        if not prompt_template:
            out[angle] = AssetResult(
                name=f"{character.name}/{angle}",
                image_path=str(character_angle_path(character.name, angle, assets_dir)),
                source="error",
                error=f"unknown angle: {angle}",
            )
            continue
        prompt = prompt_template.format(
            style_anchor=_safe_style_anchor(style_anchor),
            appearance=character.appearance or "(no appearance)",
            palette=", ".join(character.color_palette) if character.color_palette else "neutral",
        )
        out[angle] = _generate_one_angle(
            character.name, angle, prompt, image_backend,
            assets_dir, force_regenerate, log,
        )

    return out


# ---------- 生成 scene sheet ----------

def generate_scene_sheet(
    scene: SceneSpec,
    image_backend: ie.ImageBackend,
    *,
    style_anchor: str = "",
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
) -> AssetResult:
    """为单个 scene 生成 scene sheet。缓存同 character_sheet。"""
    log = log_callback or (lambda msg: print(msg, flush=True))
    sd = scene_dir(assets_dir)
    sd.mkdir(parents=True, exist_ok=True)

    img_path = scene_path(scene.id, assets_dir)
    url_path = scene_url_path(scene.id, assets_dir)

    if not force_regenerate and has_cached_scene(scene.id, assets_dir):
        cached_url = url_path.read_text(encoding="utf-8").strip()
        log(f"[asset-prep] cache hit: scene_{scene.id}")
        return AssetResult(
            name=f"scene_{scene.id}",
            image_path=str(img_path),
            url=cached_url,
            source="cached",
            extra={"path": str(img_path), "scene_id": scene.id},
        )

    prompt = SCENE_SHEET_PROMPT.format(
        style_anchor=_safe_style_anchor(style_anchor),
        location=scene.location or "(no location)",
        atmosphere=scene.atmosphere or "neutral",
        key_elements=", ".join(scene.key_elements) if scene.key_elements else "general",
    )

    started = time.monotonic()
    try:
        results = image_backend.generate(
            prompt,
            aspect_ratio_value="16:9",
            response_format_value="url",
        )
    except Exception as exc:
        return AssetResult(
            name=f"scene_{scene.id}",
            image_path=str(img_path),
            source="error",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
        )
    elapsed = time.monotonic() - started

    r = results[0]
    if not r.image_url:
        return AssetResult(
            name=f"scene_{scene.id}",
            image_path=str(img_path),
            source="error",
            error="response_format=url 但未拿到 image_url",
            duration_seconds=elapsed,
        )

    try:
        data = urllib.request.urlopen(r.image_url[0], timeout=30).read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return AssetResult(
            name=f"scene_{scene.id}",
            image_path=str(img_path),
            source="error",
            error=f"下载 url 失败：{exc}",
            duration_seconds=elapsed,
        )

    img_path.write_bytes(data)
    url_path.write_text(r.image_url[0], encoding="utf-8")
    log(f"[asset-prep] generated: scene_{scene.id} → {img_path} ({len(data)} bytes)")

    return AssetResult(
        name=f"scene_{scene.id}",
        image_path=str(img_path),
        url=r.image_url[0],
        url_expires_at=_extract_url_expiry(r.image_url[0]),
        source="ok",
        duration_seconds=elapsed,
        extra={"bytes": len(data), "scene_id": scene.id},
    )


# ---------- 批量准备 ----------

def prepare_all_characters(
    storyboard: Storyboard,
    image_backend: ie.ImageBackend,
    *,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, dict[str, AssetResult]]:
    """为每个唯一角色生成多角度 character sheet。返回 {name: {angle: AssetResult}}。"""
    log = log_callback or (lambda msg: print(msg, flush=True))
    style_anchor = getattr(storyboard, "style_anchor", "") or ""
    out: dict[str, dict[str, AssetResult]] = {}
    seen: dict[str, Character] = {}
    for c in storyboard.characters or []:
        seen[c.name] = c
    if not seen:
        log("[asset-prep] storyboard.characters 为空，跳过")
        return out
    log(f"[asset-prep] preparing {len(seen)} character(s) × {len(DEFAULT_ANGLES)} angles")
    for name, ch in seen.items():
        angle_results = generate_character_sheet(
            ch, image_backend,
            style_anchor=style_anchor,
            assets_dir=assets_dir,
            force_regenerate=force_regenerate,
            log_callback=log,
        )
        out[name] = angle_results
    return out


def prepare_all_scenes(
    storyboard: Storyboard,
    image_backend: ie.ImageBackend,
    *,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
) -> dict[int, AssetResult]:
    """为每个唯一 scene 生成 scene sheet。"""
    log = log_callback or (lambda msg: print(msg, flush=True))
    style_anchor = getattr(storyboard, "style_anchor", "") or ""
    out: dict[int, AssetResult] = {}
    seen: dict[int, SceneSpec] = {}
    for s in storyboard.scenes or []:
        seen[s.id] = s
    if not seen:
        log("[asset-prep] storyboard.scenes 为空，跳过")
        return out
    log(f"[asset-prep] preparing {len(seen)} scene sheet(s)...")
    for sid, sc in seen.items():
        r = generate_scene_sheet(
            sc, image_backend,
            style_anchor=style_anchor,
            assets_dir=assets_dir,
            force_regenerate=force_regenerate,
            log_callback=log,
        )
        out[sid] = r
    return out


# ---------- V3 兼容（单角度 front-only） ----------

def prepare_all_characters_v3_single_angle(
    storyboard: Storyboard,
    image_backend: ie.ImageBackend,
    *,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, AssetResult]:
    """V3 兼容：只生成 front 角度。返回 {name: AssetResult}（V3 接口）。"""
    log = log_callback or (lambda msg: print(msg, flush=True))
    style_anchor = getattr(storyboard, "style_anchor", "") or ""
    out: dict[str, AssetResult] = {}
    seen: dict[str, Character] = {}
    for c in storyboard.characters or []:
        seen[c.name] = c
    if not seen:
        return out
    for name, ch in seen.items():
        angle_results = generate_character_sheet(
            ch, image_backend,
            style_anchor=style_anchor,
            assets_dir=assets_dir,
            force_regenerate=force_regenerate,
            log_callback=log,
            angles=("front",),
        )
        front = angle_results.get("front")
        if front:
            out[name] = front
    return out


def build_character_reference_lookup(
    assets: dict[str, AssetResult],
) -> dict[str, str]:
    """V3 兼容：把单角度 AssetResult 列表压成 {name: url}。"""
    return {name: r.url for name, r in assets.items() if r.ok and r.url}


# ---------- 生成 character 表情 sheet ----------

def generate_character_expressions(
    character: Character,
    image_backend: ie.ImageBackend,
    *,
    style_anchor: str = "",
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
    expressions: tuple[str, ...] = DEFAULT_EXPRESSIONS,
) -> dict[str, AssetResult]:
    """为单个角色生成多表情 sheet（独立于 angle sheets）。

    返回 {expression: AssetResult}。
    缓存路径：assets/characters/<name>/expression_<expr>.jpg + .url.txt
    """
    log = log_callback or (lambda msg: print(msg, flush=True))
    cd = character_subdir(character.name, assets_dir)
    cd.mkdir(parents=True, exist_ok=True)

    out: dict[str, AssetResult] = {}
    log(f"[asset-prep] preparing {len(expressions)} expression(s) for {character.name}: {expressions}")

    for expr in expressions:
        prompt_template = EXPRESSION_PROMPTS.get(expr)
        if not prompt_template:
            out[expr] = AssetResult(
                name=f"{character.name}/expression_{expr}",
                image_path=str(character_expression_path(character.name, expr, assets_dir)),
                source="error",
                error=f"unknown expression: {expr}",
            )
            continue
        prompt = prompt_template.format(
            style_anchor=_safe_style_anchor(style_anchor),
            appearance=character.appearance or "(no appearance)",
            palette=", ".join(character.color_palette) if character.color_palette else "neutral",
        )
        out[expr] = _generate_expression_one(
            character.name, expr, prompt, image_backend,
            assets_dir, force_regenerate, log,
        )
    return out


def _generate_expression_one(
    name: str,
    expression: str,
    prompt: str,
    image_backend: ie.ImageBackend,
    assets_dir: Path,
    force_regenerate: bool,
    log: Callable[[str], None],
) -> AssetResult:
    img_path = character_expression_path(name, expression, assets_dir)
    url_path = character_expression_url_path(name, expression, assets_dir)

    if not force_regenerate and has_cached_character_expression(name, expression, assets_dir):
        cached_url = url_path.read_text(encoding="utf-8").strip()
        log(f"[asset-prep] cache hit: {name}/expression_{expression}")
        return AssetResult(
            name=f"{name}/expression_{expression}",
            image_path=str(img_path),
            url=cached_url,
            source="cached",
            extra={"path": str(img_path), "expression": expression},
        )

    started = time.monotonic()
    try:
        results = image_backend.generate(
            prompt,
            aspect_ratio_value="1:1",
            response_format_value="url",
        )
    except Exception as exc:
        return AssetResult(
            name=f"{name}/expression_{expression}",
            image_path=str(img_path),
            source="error",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
            extra={"expression": expression},
        )
    elapsed = time.monotonic() - started

    r = results[0]
    if not r.image_url:
        return AssetResult(
            name=f"{name}/expression_{expression}",
            image_path=str(img_path),
            source="error",
            error="response_format=url 但未拿到 image_url",
            duration_seconds=elapsed,
            extra={"expression": expression},
        )

    try:
        data = urllib.request.urlopen(r.image_url[0], timeout=30).read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return AssetResult(
            name=f"{name}/expression_{expression}",
            image_path=str(img_path),
            source="error",
            error=f"下载 url 失败：{exc}",
            duration_seconds=elapsed,
            extra={"expression": expression},
        )

    img_path.write_bytes(data)
    url_path.write_text(r.image_url[0], encoding="utf-8")
    log(f"[asset-prep] generated: {name}/expression_{expression} → {img_path} ({len(data)} bytes)")
    return AssetResult(
        name=f"{name}/expression_{expression}",
        image_path=str(img_path),
        url=r.image_url[0],
        url_expires_at=_extract_url_expiry(r.image_url[0]),
        source="ok",
        duration_seconds=elapsed,
        extra={"bytes": len(data), "expression": expression},
    )


def prepare_all_expressions(
    storyboard: Storyboard,
    image_backend: ie.ImageBackend,
    *,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, dict[str, AssetResult]]:
    """每个唯一角色 × DEFAULT_EXPRESSIONS 表情。返回 {name: {expression: AssetResult}}。"""
    log = log_callback or (lambda msg: print(msg, flush=True))
    style_anchor = getattr(storyboard, "style_anchor", "") or ""
    out: dict[str, dict[str, AssetResult]] = {}
    seen: dict[str, Character] = {}
    for c in storyboard.characters or []:
        seen[c.name] = c
    if not seen:
        log("[asset-prep] storyboard.characters 为空，跳过 expressions")
        return out
    log(f"[asset-prep] preparing {len(seen)} character(s) × {len(DEFAULT_EXPRESSIONS)} expressions")
    for name, ch in seen.items():
        out[name] = generate_character_expressions(
            ch, image_backend,
            style_anchor=style_anchor,
            assets_dir=assets_dir,
            force_regenerate=force_regenerate,
            log_callback=log,
        )
    return out


# ---------- 生成 scene variant sheet ----------

def generate_scene_variants(
    scene: SceneSpec,
    image_backend: ie.ImageBackend,
    *,
    style_anchor: str = "",
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
    variants: tuple[str, ...] = DEFAULT_SCENE_VARIANTS,
) -> dict[str, AssetResult]:
    """为单个 scene 生成 3 个时间变体 sheet。返回 {variant: AssetResult}。"""
    log = log_callback or (lambda msg: print(msg, flush=True))
    sd = scene_dir(assets_dir)
    sd.mkdir(parents=True, exist_ok=True)

    out: dict[str, AssetResult] = {}
    log(f"[asset-prep] preparing {len(variants)} variant(s) for scene_{scene.id}: {variants}")

    for variant in variants:
        prompt_template = SCENE_VARIANT_PROMPTS.get(variant)
        if not prompt_template:
            out[variant] = AssetResult(
                name=f"scene_{scene.id}_{variant}",
                image_path=str(scene_variant_path(scene.id, variant, assets_dir)),
                source="error",
                error=f"unknown variant: {variant}",
            )
            continue
        prompt = prompt_template.format(
            style_anchor=_safe_style_anchor(style_anchor),
            location=scene.location or "(no location)",
            key_elements=", ".join(scene.key_elements) if scene.key_elements else "general",
        )
        out[variant] = _generate_scene_variant_one(
            scene.id, variant, prompt, image_backend,
            assets_dir, force_regenerate, log,
        )
    return out


def _generate_scene_variant_one(
    scene_id: int,
    variant: str,
    prompt: str,
    image_backend: ie.ImageBackend,
    assets_dir: Path,
    force_regenerate: bool,
    log: Callable[[str], None],
) -> AssetResult:
    img_path = scene_variant_path(scene_id, variant, assets_dir)
    url_path = scene_variant_url_path(scene_id, variant, assets_dir)

    if not force_regenerate and has_cached_scene_variant(scene_id, variant, assets_dir):
        cached_url = url_path.read_text(encoding="utf-8").strip()
        log(f"[asset-prep] cache hit: scene_{scene_id}_{variant}")
        return AssetResult(
            name=f"scene_{scene_id}_{variant}",
            image_path=str(img_path),
            url=cached_url,
            source="cached",
            extra={"path": str(img_path), "variant": variant},
        )

    started = time.monotonic()
    try:
        results = image_backend.generate(
            prompt,
            aspect_ratio_value="16:9",
            response_format_value="url",
        )
    except Exception as exc:
        return AssetResult(
            name=f"scene_{scene_id}_{variant}",
            image_path=str(img_path),
            source="error",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
            extra={"variant": variant},
        )
    elapsed = time.monotonic() - started

    r = results[0]
    if not r.image_url:
        return AssetResult(
            name=f"scene_{scene_id}_{variant}",
            image_path=str(img_path),
            source="error",
            error="response_format=url 但未拿到 image_url",
            duration_seconds=elapsed,
            extra={"variant": variant},
        )

    try:
        data = urllib.request.urlopen(r.image_url[0], timeout=30).read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return AssetResult(
            name=f"scene_{scene_id}_{variant}",
            image_path=str(img_path),
            source="error",
            error=f"下载 url 失败：{exc}",
            duration_seconds=elapsed,
            extra={"variant": variant},
        )

    img_path.write_bytes(data)
    url_path.write_text(r.image_url[0], encoding="utf-8")
    log(f"[asset-prep] generated: scene_{scene_id}_{variant} → {img_path} ({len(data)} bytes)")
    return AssetResult(
        name=f"scene_{scene_id}_{variant}",
        image_path=str(img_path),
        url=r.image_url[0],
        url_expires_at=_extract_url_expiry(r.image_url[0]),
        source="ok",
        duration_seconds=elapsed,
        extra={"bytes": len(data), "variant": variant},
    )


def prepare_all_scene_variants(
    storyboard: Storyboard,
    image_backend: ie.ImageBackend,
    *,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    force_regenerate: bool = False,
    log_callback: Callable[[str], None] | None = None,
) -> dict[int, dict[str, AssetResult]]:
    """每个唯一 scene × DEFAULT_SCENE_VARIANTS 变体。返回 {scene_id: {variant: AssetResult}}。"""
    log = log_callback or (lambda msg: print(msg, flush=True))
    style_anchor = getattr(storyboard, "style_anchor", "") or ""
    out: dict[int, dict[str, AssetResult]] = {}
    seen: dict[int, SceneSpec] = {}
    for s in storyboard.scenes or []:
        seen[s.id] = s
    if not seen:
        log("[asset-prep] storyboard.scenes 为空，跳过 variants")
        return out
    log(f"[asset-prep] preparing {len(seen)} scene(s) × {len(DEFAULT_SCENE_VARIANTS)} variants")
    for sid, sc in seen.items():
        out[sid] = generate_scene_variants(
            sc, image_backend,
            style_anchor=style_anchor,
            assets_dir=assets_dir,
            force_regenerate=force_regenerate,
            log_callback=log,
        )
    return out


# ---------- Mood / Time picker ----------

# mood → preferred expression
MOOD_TO_EXPRESSION = {
    # 平和 / 写意 / 讽刺 → 平（neutral）
    "平和": "neutral", "平静": "neutral", "宁静": "neutral", "写意": "neutral",
    "讽刺": "neutral", "荒诞": "neutral",
    # 欢喜 → 笑
    "欢喜": "happy", "开心": "happy", "快乐": "happy", "喜悦": "happy",
    "温馨": "happy", "希望": "happy",
    # 急促 / 悲哀 / 意外 → 怒 (distressed)
    "急促": "angry_sad", "紧张": "angry_sad", "慌张": "angry_sad", "惊恐": "angry_sad",
    "悲哀": "angry_sad", "悲伤": "angry_sad", "凄惨": "angry_sad",
    "意外": "angry_sad", "惊讶": "angry_sad", "震惊": "angry_sad",
}


def pick_expression_for_mood(
    mood: str | list[str],
    *,
    available_expressions: tuple[str, ...] = DEFAULT_EXPRESSIONS,
    shot_index: int | None = None,                # V2.6：picker 警告用
) -> str:
    """按 mood 选最佳 expression。fallback → available_expressions[0]。

    V2.6: 返回前用 quality.warn_picker_fallback() 告诉用户走了 fallback。
    """
    moods = mood if isinstance(mood, list) else [mood]
    requested = ",".join(moods) if moods else "(none)"
    chosen = None
    for m in moods:
        s = (m or "").strip()
        for k, v in MOOD_TO_EXPRESSION.items():
            if k == s and v in available_expressions:
                chosen = v
                break
        if chosen:
            break
        for k, v in MOOD_TO_EXPRESSION.items():
            if k in s or s in k:
                if v in available_expressions:
                    chosen = v
                    break
        if chosen:
            break
    if not chosen:
        chosen = available_expressions[0] if available_expressions else "neutral"
    # V2.6：picker fallback 警告（避免静默降级）
    if shot_index is not None:
        from hailuo_video.quality import warn_picker_fallback
        warn_picker_fallback("expression", shot_index, requested, chosen)
    return chosen


# Shot.time → scene variant
TIME_TO_SCENE_VARIANT = {
    "白天": "day", "日": "day", "晨": "day", "午": "day", "午间": "day",
    "清晨": "day", "上午": "day", "下午": "day",
    "黄昏": "sunset", "夕阳": "sunset", "暮": "sunset", "傍晚": "sunset", "夕": "sunset",
    "夜": "night", "夜晚": "night", "月": "night", "月夜": "night", "深夜": "night",
}


def pick_scene_variant_for_time(
    time: str,
    *,
    available_variants: tuple[str, ...] = DEFAULT_SCENE_VARIANTS,
    shot_index: int | None = None,                # V2.6：picker 警告用
) -> str:
    """按 shot.time 选 scene variant。fallback → available_variants[0]。"""
    s = (time or "").strip()
    requested = s or "(none)"
    chosen = None
    for k, v in TIME_TO_SCENE_VARIANT.items():
        if k == s and v in available_variants:
            chosen = v
            break
    if not chosen:
        for k, v in TIME_TO_SCENE_VARIANT.items():
            if k in s or s in k:
                if v in available_variants:
                    chosen = v
                    break
    if not chosen:
        chosen = available_variants[0] if available_variants else "day"
    # V2.6：picker fallback 警告
    if shot_index is not None:
        from hailuo_video.quality import warn_picker_fallback
        warn_picker_fallback("scene_variant", shot_index, requested, chosen)
    return chosen


# ---------- Angle picker（V2.5.2：shot_type + camera_motion） ----------

# shot_type → preferred angle 基线映射（V2.5）
SHOT_TYPE_TO_ANGLE = {
    # 远景 / 背影 → 背面 reference（场景大，人物小，背影合适）
    "远景": "back",
    "wide": "back",
    "establishing": "back",
    "背影": "back",
    "拉远": "back",
    # 中景 / 过肩 / 侧面 → 侧面 reference
    "中景": "side_left",       # V2.5.2：默认左侧；pan 时会被 camera_motion 覆盖
    "medium": "side_left",
    "过肩": "side_left",
    "侧面": "side_left",
    "pan": "side_left",
    "跟随": "side_left",
    # 近景 / 特写 / 正面 → 正面 reference
    "近景": "front",
    "特写": "front",
    "近": "front",
    "close": "front",
    "正面": "front",
    "俯拍": "front",
    "仰拍": "front",
}


# V2.5.2 新增：camera_motion → angle override（camera 方向决定看角色哪侧）
# pan_left  → 摄像机向左摇 → 看到角色**右**侧 → 用 side_right
# pan_right → 摄像机向右摇 → 看到角色**左**侧 → 用 side_left
CAMERA_MOTION_TO_ANGLE = {
    "pan_left": "side_right",
    "pan right": "side_right",
    "pan-left": "side_right",
    "左摇": "side_right",
    "向左": "side_right",

    "pan_right": "side_left",
    "pan right": "side_left",       # 同上但右
    "右摇": "side_left",
    "向右": "side_left",

    "pan_up": "front",             # 仰拍 → 正面
    "仰拍": "front",
    "tilt_up": "front",

    "pan_down": "back",
    "俯拍": "back",
    "tilt_down": "back",

    "推近": "front",                 # 推近 → 特写级 → 正面
    "推": "front",
    "zoom_in": "front",

    "拉远": "back",                  # 拉远 → 远景级 → 背面
    "拉": "back",
    "zoom_out": "back",
}


def pick_angle_for_motion(camera_motion: str) -> str | None:
    """camera_motion 单独查 → 返回 angle（含 side_left/right 特化）。"""
    if not camera_motion:
        return None
    s = camera_motion.strip().lower()
    # 优先精确匹配
    for k, v in CAMERA_MOTION_TO_ANGLE.items():
        if k.lower() == s:
            return v
    # 子串匹配
    for k, v in CAMERA_MOTION_TO_ANGLE.items():
        if k.lower() in s or s in k.lower():
            return v
    return None


def pick_angle_for_shot(
    shot_type: str,
    *,
    camera_motion: str = "",
    available_angles: tuple[str, ...] = DEFAULT_ANGLES,
    shot_index: int | None = None,                # V2.6：picker 警告用
) -> str:
    """V2.5.2：先按 camera_motion 选，缺则按 shot_type，再 fallback available_angles[0]。

    V2.6: picker fallback 警告（避免静默降级）。
    """
    requested = f"shot_type={shot_type!r}, camera_motion={camera_motion!r}"

    # 1) camera_motion 优先（最具体）
    motion_angle = pick_angle_for_motion(camera_motion)
    if motion_angle and motion_angle in available_angles:
        if shot_index is not None and motion_angle != available_angles[0]:
            from hailuo_video.quality import warn_picker_fallback
            warn_picker_fallback("angle", shot_index, requested, motion_angle)
        return motion_angle
    # 2) shot_type 基线
    s = (shot_type or "").strip().lower()
    chosen = None
    for k, v in SHOT_TYPE_TO_ANGLE.items():
        if k.lower() == s and v in available_angles:
            chosen = v
            break
    if not chosen:
        for k, v in SHOT_TYPE_TO_ANGLE.items():
            if k.lower() in s or s in k.lower():
                if v in available_angles:
                    chosen = v
                    break
    if not chosen:
        chosen = available_angles[0] if available_angles else "front"
    if shot_index is not None:
        from hailuo_video.quality import warn_picker_fallback
        warn_picker_fallback("angle", shot_index, requested, chosen)
    return chosen


def build_angle_url_lookup(
    char_assets: dict[str, dict[str, AssetResult]],
) -> dict[str, dict[str, str]]:
    """从 multi-angle assets 压成 {name: {angle: url}}。"""
    out: dict[str, dict[str, str]] = {}
    for name, angle_results in char_assets.items():
        out[name] = {a: r.url for a, r in angle_results.items() if r.ok and r.url}
    return out


# image-01 一次最多 4 个 subject_references（实测稳定）
MAX_SUBJECT_REFERENCES = 4


@dataclass(frozen=True)
class ShotReferenceUrls:
    """一个 shot 的 i2i 锚定结果。只含 http(s) URL，不含角色名。"""
    urls: list[str]
    labels: list[str]
    scene_urls: list[str]


def _read_cached_url(path: Path) -> str:
    """读 .url.txt。非 http(s) 或文件缺失时返回空串。"""
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    return ""


def infer_shot_time(shot: Any) -> str:
    """shot.time 优先；为空时从 description / mood 推断昼夜。"""
    shot_time = (getattr(shot, "time", "") or "").strip()
    if shot_time:
        return shot_time
    mood = list(getattr(shot, "mood", []) or [])
    desc = getattr(shot, "description", "") or ""
    blob = f"{desc} {' '.join(str(m) for m in mood)}"
    for key in ("黄昏", "夕阳", "暮", "夕"):
        if key in blob:
            return "黄昏"
    for key in ("夜", "月", "深夜"):
        if key in blob:
            return "夜"
    for key in ("晨", "午", "白天"):
        if key in blob:
            return "白天"
    return ""


def resolve_shot_reference_urls(
    shot: Any,
    assets_dir: str | Path = DEFAULT_ASSETS_DIR,
    *,
    shot_index: int | None = None,
) -> ShotReferenceUrls:
    """按 4 轴 picker 从 assets 缓存收集本镜 subject URL。

    顺序与 V2 pipeline 一致：每个在场角色的角度 URL、表情 URL，然后场景时间变体。
    合计最多 MAX_SUBJECT_REFERENCES 个。缓存缺失的轴跳过，不把角色名当作参考图。
    V2 `run_pipeline` 与 V3 orchestrator 都走这个函数，避免两条管线各选各的。
    """
    root = Path(assets_dir)
    urls: list[str] = []
    labels: list[str] = []
    scene_urls: list[str] = []

    def _add(url: str, label: str, *, scene: bool = False) -> None:
        if not url or url in urls or len(urls) >= MAX_SUBJECT_REFERENCES:
            return
        urls.append(url)
        labels.append(label)
        if scene:
            scene_urls.append(url)

    for name in list(getattr(shot, "characters", []) or []):
        angle = pick_angle_for_shot(
            getattr(shot, "shot_type", "") or "",
            camera_motion=getattr(shot, "camera_motion", "") or "",
            shot_index=shot_index,
        )
        _add(
            _read_cached_url(character_angle_url_path(name, angle, root)),
            f"{name}/{angle}",
        )
        expression = pick_expression_for_mood(
            list(getattr(shot, "mood", []) or []),
            shot_index=shot_index,
        )
        _add(
            _read_cached_url(character_expression_url_path(name, expression, root)),
            f"{name}/expression_{expression}",
        )

    scene_id = getattr(shot, "scene_id", None)
    if scene_id:
        variant = pick_scene_variant_for_time(
            infer_shot_time(shot),
            shot_index=shot_index,
        )
        scene_url = _read_cached_url(scene_variant_url_path(int(scene_id), variant, root))
        scene_label = f"scene_{int(scene_id)}/{variant}"
        if not scene_url:
            scene_url = _read_cached_url(scene_url_path(int(scene_id), root))
            scene_label = f"scene_{int(scene_id)}/default"
        if scene_url and scene_url not in urls and len(urls) >= MAX_SUBJECT_REFERENCES:
            # 场景 URL 已解析，但合并列表已满。scene_urls 仍记录，调用方不要再塞进第 5 个 ref。
            scene_urls.append(scene_url)
        else:
            _add(scene_url, scene_label, scene=True)

    return ShotReferenceUrls(urls=urls, labels=labels, scene_urls=scene_urls)
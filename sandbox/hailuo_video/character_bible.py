"""V3.8 W1 — 全局角色圣经（Global Character Bible）。

解决仓库里三个已确认的活问题：

1. **角色跨天漂移**：character sheet 每个项目/每天重新生成 → Day1 的拉布拉多和
   Day2 不是同一套画（`sample/story_arc_xiaohei_solo/README.md:259-262`）。
2. **reference URL 24h 过期且不校验**：跨天跑时角色一致性参考**静默**失效
   （`sample/story_arc_xiaohei_solo/README.md:237-241`）。
3. **中文角色名歧义**："小黄" 在训练数据里高频指向橘猫 —— 即便 first_frame 是狗，
   Hailuo 仍出猫（`sample/story_arc_xiaohuang_daily/README.md:55-64`）。

## 设计依据（文献）

- **arXiv:2512.16954**「Lights, Camera, Consistency」：LLM → 制作脚本 → T2I 产出
  一致的角色视觉 → 作为锚点驱动视频。消融显示**去掉视觉锚定后角色一致性从 7.99
  崩到 0.55**。→ 锚点是硬需求，但锚点必须**稳定复用**，而不是每个项目重画。
- **arXiv:2503.10391**「CINEMA」：多主体一致性必须靠 MLLM 语义引导，
  **不能靠「参考图 ↔ 关键词」映射**（后者引入歧义）。
  → 身份键**不含角色名**：名字只是展示标签，视觉属性才是身份。
- **arXiv:2506.07848**「PolyVivid」：把视觉身份嵌进文本空间做精确 grounding。
  → 身份键由 appearance（视觉可判定属性）决定。

## 身份键怎么算

```
key = sha256( normalize(appearance) | normalize(color_palette) | normalize(style_anchor) )[:16]
```

- **不含 `name`** → 角色改名不换身份（跨天复用不断链）。
- **含 appearance 全文（含颜色词）** → "奶黄拉布拉多" 与 "纯黑拉布拉多" 必须是两个
  不同身份（这正是小黄/小黑场景）。颜色**不能**从键里剔除。
- **含 style_anchor** → 不同画风下的同一角色是不同视觉身份，不共享参考图
  （否则串味）。

## 目录布局

```
sandbox/assets/character_bible/{character_key}/     ← 全局库（跨项目共享）
    identity.json
    front.jpg  front.url.txt  front.expires.txt
    ...
sandbox/assets/characters/{project_id}/{name}/      ← 项目内 cache（ADR-007 原路径，不变）
```

全局库在 ADR-007 的 project namespace **之外**（`ProjectContext.assets_dir` =
`sandbox/assets/characters/{project_id}`），因此 `ProjectRegistry.cleanup`
不会误删全局库，storyboard / 产物仍严格按项目隔离。

## 新鲜度语义（关键）

- **本地路径**（Gemini `inlineData`）→ **永远新鲜**：没有 TTL 问题，跨天完全一致。
- **http(s) URL**（MiniMax `subject_references` 只吃 URL）→ 受签发 TTL 约束。
  剩余 TTL 不足 → 判**不新鲜** → 调用方必须跳过并告警，**不得静默使用**
  （旧行为就是静默用过期 URL → 角色一致性悄悄消失）。

R4：本模块只做纯代码判定，**不调 LLM**（名字歧义守卫是确定性规则）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 全局库根目录。**必须在项目 namespace 之外**（ADR-007 隔离不受影响）。
# 可用 env `CHARACTER_BIBLE_DIR` 覆盖（测试用临时目录；部署可指向共享盘）。
DEFAULT_BIBLE_DIR = Path("sandbox/assets/character_bible")

# 剩余 TTL 低于此值 → 判不新鲜（留 1h 余量，避免跑到一半过期）
DEFAULT_REFERENCE_TTL_SECONDS = 3600

# URL 没有 Expires 参数时的兜底 TTL（MiniMax 签名 URL 实测约 24h）
DEFAULT_URL_TTL_SECONDS = 24 * 3600

# 中文"颜色 + 尺寸"式角色名在训练数据里高频指向特定物种（橘猫 / 白猫）。
# 纯代码门禁（不调 LLM），命中只告警不阻断 —— 阻断会误伤合法用法。
AMBIGUOUS_COLOR_NAMES: tuple[str, ...] = (
    "小黄", "小白", "小黑", "小灰", "小棕", "小花",
    "黄猫", "白猫", "黑猫", "灰猫",
)

# 犬类关键词 —— 与歧义色名同时出现时，几乎必然踩"小黄变猫"事故
_DOG_WORDS: tuple[str, ...] = ("犬", "狗", "拉布拉多", "柯基", "柴犬", "边牧", "puppy", "dog")
_CAT_WORDS: tuple[str, ...] = ("猫", "cat", "kitten")


def default_bible_dir() -> Path:
    """全局库根目录（运行时解析，可被 env CHARACTER_BIBLE_DIR 覆盖）。

    env 覆盖用于：测试隔离（临时目录，不污染仓库）、多机部署（共享盘）。
    """
    import os
    override = os.environ.get("CHARACTER_BIBLE_DIR", "").strip()
    return Path(override) if override else DEFAULT_BIBLE_DIR


# ---------- 归一化 ----------

def normalize_identity_text(text: str) -> str:
    """身份键用的文本归一化：小写、去空白与标点、全角→半角。

    只做"同一个视觉身份写成两种排版"这类无害差异的归一化；
    **不做**语义改写（那是 LLM 的活，不该在这里）。
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in str(text).strip().lower():
        code = ord(ch)
        if code == 0x3000:                      # 全角空格
            continue
        if 0xFF01 <= code <= 0xFF5E:            # 全角 → 半角
            half = chr(code - 0xFEE0)
            if half.isalnum():
                out.append(half)
            continue
        if ch.isalnum():
            out.append(ch)
            continue
        # 空白与标点一律丢弃（"奶黄 拉布拉多" == "奶黄拉布拉多"）
    return "".join(out)


def compute_character_key(character: Any, style_anchor: str = "") -> str:
    """角色身份键 = sha256(appearance | palette | style_anchor)[:16]。

    不含 `name`（见模块 docstring 的文献依据）。

    Args:
        character: 任意带 `.appearance` / `.color_palette` / `.name` 属性的对象
            （`script_engine.Character` 或等价的 dict / 命名空间）
        style_anchor: 项目级画风锚点；不同画风 = 不同视觉身份

    Returns:
        16 位十六进制身份键；appearance 与 style_anchor 都为空时返回 ""（不建库）
    """
    appearance = _attr(character, "appearance", "")
    palette = _attr(character, "color_palette", "") or ""
    if isinstance(palette, (list, tuple)):
        palette = ",".join(str(p) for p in palette)
    norm = "|".join([
        normalize_identity_text(str(appearance)),
        normalize_identity_text(str(palette)),
        normalize_identity_text(str(style_anchor)),
    ])
    if not norm.strip("|"):
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _attr(obj: Any, name: str, default: Any = "") -> Any:
    """取属性（对象或 dict 都支持）。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------- 名字歧义守卫 ----------

def check_name_ambiguity(character: Any) -> list[str]:
    """中文色词角色名的歧义告警（纯代码判定，不调 LLM）。

    已知事故（2026-10-13「小黄变猫」）：角色名 "小黄" 在训练数据里高频对应橘猫，
    即便 first_frame 是狗，Hailuo 仍出猫。修法是改用品种名（"中华田园犬幼犬"）
    或英文（"Puppy"）。本函数只**告警**，不阻断 —— 阻断会误伤合法用法。
    """
    name = str(_attr(character, "name", "") or "").strip()
    appearance = str(_attr(character, "appearance", "") or "")
    if not name:
        return []
    warns: list[str] = []
    if name in AMBIGUOUS_COLOR_NAMES:
        warns.append(
            f"角色名 {name!r} 是中文歧义色词（训练数据里高频指向橘猫/白猫）："
            f"建议改用品种名（如「中华田园犬幼犬」）或英文（Puppy）"
        )
        low = appearance.lower()
        if any(k in low for k in _DOG_WORDS):
            warns.append(
                f"角色名 {name!r} 与 appearance 的犬种描述冲突 —— 已知事故："
                f"即便 first_frame 是狗，模型仍出猫（见 sample/story_arc_xiaohuang_daily/README.md）"
            )
        elif any(k in low for k in _CAT_WORDS):
            warns.append(
                f"角色名 {name!r} 是歧义色词且 appearance 描述为猫类，"
                f"模型极易画成猫而非犬"
            )
    return warns


# ---------- 新鲜度 ----------

def is_reference_fresh(
    url: str,
    expires_at: str = "",
    *,
    issued_at: datetime.datetime | None = None,
    ttl_seconds: int = DEFAULT_REFERENCE_TTL_SECONDS,
    now: datetime.datetime | None = None,
) -> bool:
    """reference 是否仍然可用。

    - **本地路径**（不是 http(s)）→ 永远新鲜（Gemini inlineData，无 TTL 问题）。
    - **http(s) URL**：
      - `expires_at` 可解析 → 剩余 TTL 必须 > `ttl_seconds` 才算新鲜。
      - `expires_at` 缺失但给了 `issued_at` → 用 `issued_at + DEFAULT_URL_TTL_SECONDS` 估算。
      - **两者都没有 → 视为可用**。只在**能证明过期**时才判过期：
        MiniMax 的 OSS 签名 URL 一定带 `Expires` 参数（能判定）；
        而 provider 自管的 URL 我们无从得知 TTL，硬判过期会造成误伤
        （把还能用的 reference 误杀 → 角色锚定反而丢失）。

    `expires_at` 的来源优先级：生成时写入的 `.expires.txt` sidecar → URL 的
    `Expires` 查询参数（`_extract_url_expiry`）→ 本地图片 mtime。
    """
    text = str(url or "").strip()
    if not text:
        # 空 URL = Gemini 模式（本地字节），永远新鲜
        return True
    if not text.startswith(("http://", "https://")):
        # 本地路径 → 没有过期概念
        return True
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if expires_at:
        exp = _parse_iso(expires_at)
        if exp is not None:
            return (exp - now).total_seconds() > ttl_seconds
    if issued_at is not None:
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=datetime.timezone.utc)
        return (issued_at + datetime.timedelta(seconds=DEFAULT_URL_TTL_SECONDS) - now).total_seconds() > ttl_seconds
    return True


def _parse_iso(text: str) -> datetime.datetime | None:
    """解析 ISO 时间串 → aware datetime。失败返回 None。"""
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip())
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# ---------- 身份记录 ----------

@dataclass(frozen=True)
class CharacterIdentity:
    """一个角色的视觉身份记录（落 identity.json）。"""
    key: str
    name: str = ""
    appearance: str = ""
    color_palette: tuple[str, ...] = ()
    style_anchor: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["color_palette"] = list(self.color_palette)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CharacterIdentity":
        return cls(
            key=str(data.get("key", "")),
            name=str(data.get("name", "")),
            appearance=str(data.get("appearance", "")),
            color_palette=tuple(str(p) for p in (data.get("color_palette") or [])),
            style_anchor=str(data.get("style_anchor", "")),
            created_at=str(data.get("created_at", "")),
        )


@dataclass(frozen=True)
class ReferenceEntry:
    """全局库里的一条 reference（图 + URL + 过期时间）。"""
    image_path: Path
    url: str = ""
    expires_at: str = ""
    issued_at: datetime.datetime | None = None

    def is_fresh(self, *, ttl_seconds: int = DEFAULT_REFERENCE_TTL_SECONDS) -> bool:
        return is_reference_fresh(
            self.url, self.expires_at, issued_at=self.issued_at, ttl_seconds=ttl_seconds,
        )


# ---------- 全局库 ----------

class GlobalCharacterStore:
    """跨项目共享的角色参考图库。

    用法（asset_prep 内部）：
        bible = GlobalCharacterStore()
        key = compute_character_key(character, style_anchor)
        entry = bible.read_angle(key, "front")
        if entry and entry.is_fresh():
            bible.copy_angle_into(key, "front", project_img, project_url_txt)
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_bible_dir()

    # ---------- 路径 ----------

    def dir_for(self, key: str) -> Path:
        return self.root / str(key)

    def identity_path(self, key: str) -> Path:
        return self.dir_for(key) / "identity.json"

    def angle_image_path(self, key: str, angle: str) -> Path:
        return self.dir_for(key) / f"{angle}.jpg"

    def angle_url_path(self, key: str, angle: str) -> Path:
        return self.dir_for(key) / f"{angle}.url.txt"

    def angle_expiry_path(self, key: str, angle: str) -> Path:
        return self.dir_for(key) / f"{angle}.expires.txt"

    # ---------- 身份 ----------

    def save_identity(self, identity: CharacterIdentity) -> Path:
        d = self.dir_for(identity.key)
        d.mkdir(parents=True, exist_ok=True)
        path = self.identity_path(identity.key)
        path.write_text(
            json.dumps(identity.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_identity(self, key: str) -> CharacterIdentity | None:
        path = self.identity_path(key)
        if not path.is_file():
            return None
        try:
            return CharacterIdentity.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    # ---------- reference ----------

    def has_angle(self, key: str, angle: str) -> bool:
        return self.angle_image_path(key, angle).is_file()

    def read_angle(self, key: str, angle: str) -> ReferenceEntry | None:
        """读全局库里的一条 reference（图不存在 → None）。"""
        img = self.angle_image_path(key, angle)
        if not img.is_file():
            return None
        url = ""
        url_path = self.angle_url_path(key, angle)
        if url_path.is_file():
            try:
                url = url_path.read_text(encoding="utf-8").strip()
            except OSError:
                url = ""
        expires_at = ""
        exp_path = self.angle_expiry_path(key, angle)
        if exp_path.is_file():
            try:
                expires_at = exp_path.read_text(encoding="utf-8").strip()
            except OSError:
                expires_at = ""
        issued_at = None
        if not expires_at:
            try:
                issued_at = datetime.datetime.fromtimestamp(
                    img.stat().st_mtime, tz=datetime.timezone.utc,
                )
            except OSError:
                issued_at = None
        return ReferenceEntry(
            image_path=img, url=url, expires_at=expires_at, issued_at=issued_at,
        )

    def write_angle(
        self,
        key: str,
        angle: str,
        image_path: Path | str,
        url: str = "",
        expires_at: str = "",
    ) -> bool:
        """把一条 reference 写进全局库（图 + url + 过期时间）。

        失败返回 False（**不抛** —— 全局库是优化层，不该让主流程崩）。
        """
        if not key:
            return False
        try:
            d = self.dir_for(key)
            d.mkdir(parents=True, exist_ok=True)
            src = Path(image_path)
            if not src.is_file():
                return False
            shutil.copyfile(src, self.angle_image_path(key, angle))
            self.angle_url_path(key, angle).write_text(str(url or ""), encoding="utf-8")
            self.angle_expiry_path(key, angle).write_text(str(expires_at or ""), encoding="utf-8")
            return True
        except OSError:
            return False

    def copy_angle_into(
        self,
        key: str,
        angle: str,
        dest_image: Path | str,
        dest_url: Path | str,
    ) -> bool:
        """把全局库的图复制到项目内 cache（复制而非软链 —— Windows 兼容）。

        同时把 url 与过期时间一并复制过去。
        """
        entry = self.read_angle(key, angle)
        if entry is None:
            return False
        try:
            dest_image = Path(dest_image)
            dest_url = Path(dest_url)
            dest_image.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry.image_path, dest_image)
            dest_url.write_text(entry.url, encoding="utf-8")
            if entry.expires_at:
                exp_dest = dest_image.with_suffix(".expires.txt")
                exp_dest.write_text(entry.expires_at, encoding="utf-8")
            return True
        except OSError:
            return False


__all__ = [
    "AMBIGUOUS_COLOR_NAMES",
    "DEFAULT_BIBLE_DIR",
    "DEFAULT_REFERENCE_TTL_SECONDS",
    "DEFAULT_URL_TTL_SECONDS",
    "CharacterIdentity",
    "GlobalCharacterStore",
    "ReferenceEntry",
    "check_name_ambiguity",
    "compute_character_key",
    "default_bible_dir",
    "is_reference_fresh",
    "normalize_identity_text",
]

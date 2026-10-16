"""剧本生成 Prompt 模板（中文，针对漫剧改编）。

设计原则：
  - 强约束 JSON 输出（fenced ```json），便于程序化解析
  - 提供 schema 示例让 LLM 知道字段名 / 类型
  - 镜头描述面向"AI 生图 / 生视频"的可视化关键词
"""


# ---------- 1. 小说 → 漫剧剧本 ----------

NOVEL_TO_SCRIPT_PROMPT = """你是一位资深的漫剧编剧。请把用户提供的小说片段改编为一段 30 镜头的漫剧剧本。

要求：
- 总长度 500-800 字
- 场景数量 5-8 个（每场戏含时间/地点/在场角色）
- 保留原作的核心戏剧冲突和情感节奏
- 对白尽量精炼（漫剧以视觉叙事为主）
- 末尾用 一件事 的"事"来串场（适合 5s 镜头）
- 不要改动原作的人物名

输出格式（严格 JSON，不要任何额外文字）：
```json
{{
  "title": "剧本标题（10 字以内）",
  "scenes": [
    {{
      "id": 1,
      "time": "白天 / 夜晚 / 黄昏（中文）",
      "location": "地点（中文，10 字以内）",
      "characters": ["角色 A", "角色 B"],
      "summary": "本场戏摘要（50-100 字）",
      "dialogue": "关键台词（如有，0-2 句）"
    }}
  ]
}}
```

小说片段：
{novel}
"""


# ---------- 2. 剧本 → 角色表 ----------

CHARACTER_EXTRACTION_PROMPT = """你是一位漫剧角色设计师。请从下面剧本中提取所有角色，并为每个角色生成适合 AI 生图的视觉描述。

要求：
- 每个角色包含：姓名、外貌描述（30-80 字，描述体型 / 发型 / 服饰 / 显著特征）、性格关键词（3-5 个）、服装色系（用于 AI 生图保持一致性）
- 描述面向 AI 生图：尽量具体、可视化、避免抽象形容词
- 同名角色只列一次

输出格式（严格 JSON，不要任何额外文字）：
```json
{{
  "characters": [
    {{
      "name": "角色名",
      "appearance": "外貌描述（30-80 字）",
      "personality": ["关键词1", "关键词2", "关键词3"],
      "color_palette": ["主色", "辅色", "点缀色"]
    }}
  ]
}}
```

剧本：
{script_json}
"""


# ---------- 3. 剧本 + 角色 → 分镜 ----------

# V3.2 自动开发冲刺（轮 2）：70/30 提示法 + 三拍结构
# - 70% = 可视化具体信息（人物姿态 / 道具 / 构图 / 光线 / 背景元素）
# - 30% = 艺术氛围词（情绪 / 节奏 / 风格暗示）
# 目的：让 Hailuo 真 AI 视频生成与 LLM 拆解都拿到足够的"运动语义"

CAMERA_MOTION_WHITELIST = (
    "固定", "缓慢推近", "缓慢拉远",
    "横移-左到右", "横移-右到左",
    "上移-下到上", "下移-上到下",
    "推进, 跟随", "拉远, 俯瞰",
    "环绕-顺时针", "环绕-逆时针",
    "跟随-主体运动", "摇摄-左到右",
    "摇摄-右到左", "俯仰-上到下", "俯仰-下到上",
)

MOTION_COMPLEXITY_WHITELIST = ("static", "simple", "complex")

THREE_BEAT_STRUCTURE_HINT = """
三拍结构（每场 Scene 拆为 起 / 承 / 转 / 合 四节；可省略"合"用于悬念场景）：
  起（Setup）       —— 镜头 1：建立场景与人物（建议 wide shot 或 establishing shot）
  承（Development）—— 镜头 2-3：推进情节，主交互发生（建议 medium shot / over-shoulder）
  转（Twist）       —— 镜头 4：情绪 / 关系转折点（建议 close-up，强调表情）
  合（Resolution） —— 镜头 5（如有）：本场结论 / 留下钩子（建议 wide shot 或 detail cut）

70/30 提示法（每个 description 字段）：
  - 70% = 可视化具体信息（人物姿态 + 道具 + 构图 + 光线 + 背景元素）
  - 30% = 艺术氛围词（情绪 + 节奏 + 风格暗示）
  - 禁止：完全抽象或纯情绪词（"很悲伤"、"很有气氛"）——这是 AI 生图 / 生视频的死路
  - 推荐：先写 70% 具体，再补 30% 修饰；总字数 80-150 字

subject_action 字段强制格式：
  - 必须含「动作动词 + 对象/结果」（例："转身看向窗外"、"握紧拳头"、"快速奔跑出画"）
  - 不要只写情绪（"很紧张地站着" ❌ → "握拳，肩膀微颤" ✓）
  - 长度 8-30 字；过长会被截断
"""


STORYBOARD_PROMPT = """你是一位漫剧分镜师，擅长"AI 生图 / 图生视频"友好的镜头语言。请把下面的剧本拆为 {n_shots} 个分镜。

{three_beat_hint}

{camera_motion_whitelist}

{motion_complexity_whitelist}

{prompt_70_30}

基础要求：
- 镜头数量严格 = {n_shots}
- 每个镜头 duration ∈ [3, 8] 秒（默认 5 秒）；按场景顺序排列
- 镜头类型：远景 / 中景 / 近景 / 特写 / 过肩 / 俯拍 / 仰拍 / 主观视角
- 标注涉及的角色（必须在角色表里）
- 标注画面情绪关键词 mood（用于 motion guidance）
- 对白放在 dialogue 字段；无对白留空
- 旁白放在 voiceover 字段；无旁白留空

字段约束：
- **shot_id**（必填）：格式 `shot_NNN`（NNN 从 001 起，按顺序编号）
- **description**（必填）：80-150 字，按 70/30 提示法（70% 可视化 + 30% 氛围）；禁止纯抽象 / 纯情绪词
- **camera_motion**（必填）：仅从受支持的运镜列表中选；不确定时写 "固定"
- **subject_action**（必填）：8-30 字，含「动作动词 + 对象/结果」，是 MotionGenerationAgent 的关键指令
- **duration**（必填）：3-8 秒整数
- **motion_complexity**（必填）：仅允许 "static" / "simple" / "complex"
    - "static"    → 仅 Ken Burns 推拉（零 quota）
    - "simple"    → 简单主体运动（点头/摆手/转头）→ 调 MotionGenerationAgent（耗 1 quota）
    - "complex"   → 复杂全身运动（打斗/舞蹈/走长路）→ 当前需 GPU；pipeline 警告并 fallback Ken Burns
- **dialogue**（可选）：台词；无则空字符串
- **mood**（可选）：1-3 个情绪关键词

输出格式（严格 JSON，不要任何额外文字）：
```json
{{
  "shots": [
    {{
      "shot_id": "shot_001",
      "scene_id": 1,
      "shot_type": "远景",
      "description": "镜头视觉描述（80-150 字）",
      "characters": ["角色 A"],
      "dialogue": "台词或空字符串",
      "voiceover": "旁白或空字符串",
      "mood": ["情绪关键词1", "情绪关键词2"],
      "camera_motion": "缓慢推近",
      "subject_action": "转身看向窗外",
      "duration": 5,
      "motion_complexity": "simple"
    }}
  ]
}}
```

示例 shot（仅作格式参考，不要照抄）：
```json
{{
  "shot_id": "shot_001",
  "description": "晨曦中山路边的茶摊，老农夫（灰色短褂、头顶斗笠、双手粗糙有裂口）端起粗陶茶杯抿了一口，蒸汽被风吹散。背景是远山的薄雾与一棵孤零零的老槐树。氛围清冷而有禅意。",
  "camera_motion": "缓慢推近",
  "subject_action": "端起茶杯抿一口，蒸汽从杯口飘出",
  "duration": 5,
  "motion_complexity": "simple"
}}
```

剧本：
{script_json}

角色表：
{characters_json}
"""


# ---------- V3.5 Director 升级：STORYBOARD_PROMPT_V2 ----------
# V2 在 V1 字面字符串末尾追加 "导演视角" 段 + 扩展 schema（含 director 字段）。
# 设计原则：
#   - 仍使用 4 个 format 占位符（n_shots / three_beat_hint / camera_motion_whitelist /
#     motion_complexity_whitelist / prompt_70_30 / script_json / characters_json），
#     V2 不引入新占位符，所以 test_prompts.py 的 .format() 调用仍合法
#   - 扩展 schema 写在字符串字面（不通过 format），保持向后兼容
#   - V2 由 render_storyboard_prompt(director_mode=True) 显式启用

STORYBOARD_DIRECTOR_GUIDANCE_V2 = """
V3.5 Director 升级（director_mode=True 启用）—— 导演视角（每个镜头必须含）：

- 构图（三分 / 居中 / 引导线 / 对称）
- 镜头语言（景别 + 焦段感：wide / 35mm / 50mm / 85mm / telephoto）
- 光线（主光方向 + 反差 + 特殊：剪影 / 逆光 / 侧光 / 顶光）
- 视线方向（角色看向哪 + 视线匹配 eyeline match）
- 屏幕方向（主体运动 L→R / R→L，保持 180°轴线规则）
- 节奏（hold / 留白 / 推拉速度）
- 转场意识（与前/后镜衔接：cut / dissolve / match_cut / fade）

禁止纯抽象形容词（"很温馨" ❌）→ 改为可视化描述（"暖黄金色光边勾出绒毛轮廓" ✓）。
"""

STORYBOARD_DIRECTOR_SCHEMA_V2 = """
V2 扩展字段（director_mode=True 时填写，旧字段全部保留）：
- **narrative_beat**（可选）：起 / 承 / 转 / 合（per-shot 起承转合标签）
- **pacing**（可选）：快 / 中 / 慢（影响剪辑节奏）
- **lens**（可选）：wide / 35mm / 50mm / 85mm / telephoto
- **depth_of_field**（可选）：shallow / deep
- **lighting**（可选）：主光方向 + 反差描述
- **screen_direction**（可选）：L→R / R→L / 无
- **color_grade**（可选）：teal-and-orange / 暖调 / 冷调 / 暖金色（黄昏）/ 冷蓝调（夜）
- **transition_in / transition_out**（可选）：cut / dissolve / match_cut / fade
- **bgm_cue**（可选）：背景音乐 / 音效提示
- **hook**（可选，shot_001 专属）：前 3 秒钩子文案
"""


def _build_storyboard_prompt_v2() -> str:
    """从 STORYBOARD_PROMPT 拼接 V2（保留 V1 字符串字面，末尾追加 director guidance + 扩展 schema）。

    V1 字符串保持不动（向后兼容）；V2 仅在 V1 字符串后追加段落。
    """
    # 注入点 1：在 "基础要求" 之前插入 director guidance
    v1 = STORYBOARD_PROMPT
    v2 = v1.replace(
        "基础要求：",
        STORYBOARD_DIRECTOR_GUIDANCE_V2.strip() + "\n\n基础要求：",
        1,
    )
    # 注入点 2：在 "字段约束" 之后插入扩展字段说明
    v2 = v2.replace(
        "- **mood**（可选）：1-3 个情绪关键词\n",
        "- **mood**（可选）：1-3 个情绪关键词\n" + STORYBOARD_DIRECTOR_SCHEMA_V2.strip() + "\n",
        1,
    )
    return v2


STORYBOARD_PROMPT_V2 = _build_storyboard_prompt_v2()


def render_storyboard_prompt(
    *,
    n_shots: int = 30,
    script_json: str = "",
    characters_json: str = "",
    include_hints: bool = True,
    director_mode: bool = False,
) -> str:
    """渲染 STORYBOARD_PROMPT 为最终字符串。

    include_hints=False 用于单测 / 简化版本（不含三拍 / 70/30 / 白名单提示）。
    director_mode=False（默认）→ 渲染 V1，向后兼容现有 18 个 test_prompts.py 测试。
    director_mode=True → 渲染 V2（注入 director guidance + 扩展 schema）。
    """
    if include_hints:
        three_beat_hint = THREE_BEAT_STRUCTURE_HINT.strip()
        camera_motion_whitelist = "camera_motion 白名单（仅以下 16 种）：\n" + "\n".join(
            f"  - {m}" for m in CAMERA_MOTION_WHITELIST
        )
        motion_complexity_whitelist = (
            "motion_complexity 白名单（仅 3 种）：" +
            " / ".join(MOTION_COMPLEXITY_WHITELIST)
        )
        prompt_70_30 = "70/30 提示法 + subject_action 格式要求：\n" + THREE_BEAT_STRUCTURE_HINT.strip()
    else:
        three_beat_hint = ""
        camera_motion_whitelist = ""
        motion_complexity_whitelist = ""
        prompt_70_30 = ""

    template = STORYBOARD_PROMPT_V2 if director_mode else STORYBOARD_PROMPT
    return template.format(
        n_shots=n_shots,
        three_beat_hint=three_beat_hint,
        camera_motion_whitelist=camera_motion_whitelist,
        motion_complexity_whitelist=motion_complexity_whitelist,
        prompt_70_30=prompt_70_30,
        script_json=script_json,
        characters_json=characters_json,
    )
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

STORYBOARD_PROMPT = """你是一位漫剧分镜师。请把下面的剧本拆为 {n_shots} 个 5 秒镜头分镜，每个镜头要明确可视化和镜头语言。

要求：
- 镜头数量严格 = {n_shots}
- 每个镜头 5 秒；按场景顺序排列
- 镜头描述面向 AI 生图 / 图生视频：包含人物动作、表情、构图、机位、光线、氛围
- 镜头类型：远景 / 中景 / 近景 / 特写 / 过肩 / 俯拍 / 仰拍 等
- 标注涉及的角色（必须在角色表里）
- 标注画面情绪关键词（用于视频生成的 motion guidance）
- 对白放在 dialogue 字段；无对白留空
- 旁白放在 voiceover 字段；无旁白留空
- V3.1 升级路线整合（来自 ai_video/升级路线.md）：
  - **subject_action**（必填）：每个角色在镜头中的具体动作（例："转身看向窗外"、"点头微笑"、"快速奔跑出画"）。**这句 prompt 是给 MotionGenerationAgent 用的关键指令**，决定真实 AI 视频生成的运动语义
  - **motion_complexity**（必填）：
    - "static"    → 仅 Ken Burns 推拉（已有 video_engine，无需 GPU）
    - "simple"    → 简单主体运动（点头/摆手/转头）→ 调用 MotionGenerationAgent（需 API）
    - "complex"   → 复杂全身运动（打斗/舞蹈/走长路）→ 当前需 GPU，跳过此 shot，pipeline 警告并 fallback Ken Burns

输出格式（严格 JSON，不要任何额外文字）：
```json
{{
  "shots": [
    {{
      "index": 1,
      "scene_id": 1,
      "shot_type": "远景 / 中景 / 近景 / 特写 / 过肩 / 俯拍 / 仰拍",
      "description": "镜头视觉描述（80-150 字，面向 AI 生图）",
      "characters": ["角色 A"],
      "dialogue": "台词或空字符串",
      "voiceover": "旁白或空字符串",
      "mood": ["情绪关键词1", "情绪关键词2"],
      "camera_motion": "镜头运动（如：缓慢推近 / 固定 / 横移）",
      "subject_action": "角色动作描述（必填，给真 AI 视频生成用）",
      "duration": 5,
      "motion_complexity": "static / simple / complex"
    }}
  ]
}}
```

剧本：
{script_json}

角色表：
{characters_json}
"""
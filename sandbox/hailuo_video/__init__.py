"""海螺视频（hailuo_video）RMAP 短剧生成流水线。

V2-Local 起源：因 MiniMax Plus token plan 不带 Hailuo 视频 API 权限（D-011），
原 HTTP 客户端 hailuo_client.py 被替换为本地 diffusers 推理。零外部视频网络依赖。

现已演进为多 Agent 流水线（V3.x/V4.x）：storyboard → 角色参考图（asset_prep）→
分镜图 → 运镜 → 配音 → 后期 → mp4，编排见 orchestrator.DEFAULT_DAG（8 阶段）。

V4.0 起的要点：多镜 storyboard 的产物按 shot 隔离、成片带配音与字幕、
画幅/音色按"显式值 > env > 默认"解析（不再被硬编码默认值遮蔽）。

版本号依据 docs/ISOLATION.md 标题与提交标记对齐（D-019）。
本值是 pyproject.toml 的动态版本来源，改这里即改包版本。
"""

__version__ = "4.0.0"
"""海螺视频（hailuo_video）RMAP 短剧生成流水线。

V2-Local 起源：因 MiniMax Plus token plan 不带 Hailuo 视频 API 权限（D-011），
原 HTTP 客户端 hailuo_client.py 被替换为本地 diffusers 推理。零外部视频网络依赖。

现已演进为多 Agent 流水线（V3.x）：storyboard → 角色参考图（asset_prep）→
分镜图 → 运镜 → 配音 → 后期 → mp4，编排见 orchestrator.DEFAULT_DAG（8 阶段）。

版本号依据 docs/ISOLATION.md 标题与 V3.5 P5/P6 提交标记对齐（D-019）。
本值是 pyproject.toml 的动态版本来源，改这里即改包版本。
"""

__version__ = "3.5.0"
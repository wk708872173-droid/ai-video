"""本地视频推理（CogVideoX / diffusers）客户端包。

V2-Local：因 MiniMax Plus token plan 不带 Hailuo 视频 API 权限（D-011），
原 HTTP 客户端 hailuo_client.py 已被替换。零外部网络依赖。

接口契约见 sandbox/hailuo_video/prd.md（V2-Local）。
"""

__version__ = "2.0.0"
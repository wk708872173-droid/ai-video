"""V3.1.1 验证脚本：跑一次真实 Hailuo-2.3 → 拿到 task_id → 轮询 → 下载。

用法：
    1. 确认 .env 在项目根（已含 MINIMAX_API_KEY）
    2. python scripts/test_hailuo_real.py
    3. 看 stdout 输出，把完整 JSON 发回

输出：/tmp/hailuo_smoke_*.mp4（成功）或 stdout 错误（用于修正 _default_execute）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 国内站端点（V3.1.1 更新：用户实测海外站返 401 invalid_api_key）
ENDPOINT = os.environ.get("MINIMAX_VIDEO_ENDPOINT", "https://api.minimaxi.com/v1/video_generation")
QUERY_ENDPOINT = os.environ.get("MINIMAX_VIDEO_QUERY_ENDPOINT", "https://api.minimaxi.com/v1/query/video_generation")
API_KEY = os.environ.get("MINIMAX_API_KEY", "")

if not API_KEY:
    print("[FAIL] MINIMAX_API_KEY 环境变量未设置。先 source .env 或 export 它。")
    sys.exit(1)

print(f"[INFO] endpoint = {ENDPOINT}")
print(f"[INFO] query     = {QUERY_ENDPOINT}")
print(f"[INFO] api_key   = {API_KEY[:6]}...{API_KEY[-4:]}  (length={len(API_KEY)})")


def submit(prompt, duration=6, resolution="1080P"):
    body = {"model": "MiniMax-Hailuo-2.3", "prompt": prompt, "duration": duration, "resolution": resolution}
    print(f"\n[STEP 1] submit: prompt={prompt!r} duration={duration} resolution={resolution}")
    print(f"  body = {json.dumps(body, ensure_ascii=False)}")
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            print(f"  status  = {resp.status}")
            print(f"  raw     = {raw}")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTPError = {e.code} {e.reason}")
        print(f"  body      = {body}")
        raise


def query(task_id):
    url = f"{QUERY_ENDPOINT}?task_id={task_id}"
    print(f"\n[STEP 2] query: {url}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            print(f"  status  = {resp.status}")
            print(f"  raw     = {raw}")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTPError = {e.code} {e.reason}")
        print(f"  body      = {body}")
        raise


def main():
    print("\n" + "=" * 60)
    print("Hailuo-2.3 真实 API 验证脚本 (V3.1.1)")
    print("=" * 60)

    submit_resp = submit("A cat walks slowly in a garden")

    task_id = (
        submit_resp.get("task_id")
        or submit_resp.get("id")
        or submit_resp.get("data", {}).get("task_id")
        or submit_resp.get("data", {}).get("id")
        or ""
    )
    print(f"\n  extracted task_id = {task_id!r}")

    if not task_id:
        print("[FAIL] submit 没返回 task_id。把完整 raw response 发给 Claude Code 调 _default_execute 解析。")
        sys.exit(1)

    print("\n[STEP 3] polling until Success (timeout 180s, interval 5s)")
    deadline = time.monotonic() + 180
    video_url = ""
    last_payload = None
    while time.monotonic() < deadline:
        try:
            q = query(task_id)
            last_payload = q
            status = q.get("status") or q.get("data", {}).get("status") or ""
            video_url = (
                q.get("video_url") or q.get("data", {}).get("video_url")
                or q.get("file_url") or q.get("data", {}).get("file_url")
                or ""
            )
            print(f"  status   = {status!r}, video_url = {video_url[:80]!r}")
            if status in ("Success", "Succeed", "Completed", "Done"):
                break
            if status in ("Failed", "Fail", "Error"):
                print(f"[FAIL] MiniMax returned Failed. last payload: {json.dumps(last_payload, ensure_ascii=False, indent=2)}")
                sys.exit(1)
        except Exception as exc:
            print(f"  poll error (continue): {exc}")
        time.sleep(5)

    if not video_url:
        print(f"[FAIL] 轮询超时（180s 内未 Success）。最后响应：{json.dumps(last_payload, ensure_ascii=False, indent=2)}")
        sys.exit(1)

    print(f"\n[STEP 4] download: {video_url}")
    out = Path("/tmp") / f"hailuo_smoke_{int(time.time())}.mp4"
    req = urllib.request.Request(video_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        out.write_bytes(data)
        print(f"  OK: {out} ({len(data)} bytes)")
    except Exception as exc:
        print(f"[FAIL] download failed: {exc}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("成功！请把上面所有 stdout 截图发给 Claude Code。")
    print(f"下载文件: {out}")
    print("=" * 60)


if __name__ == "__main__":
    main()

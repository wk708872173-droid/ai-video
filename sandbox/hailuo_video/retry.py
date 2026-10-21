"""V3.0 Phase 1 — 重试策略：指数退避，仅重试 transient 网络错误。

RetryPolicy 默认值参考 ai_agents_dev_tool/AGENTS.md R2（"禁止 --no-verify 修绿"），
但这里是 runtime retry，不是 test fix。

规则：
  - 仅 retry_on 列出的异常类型（URLError / TimeoutError / MiniMaxError / TTSError）
  - 不重试：API-key 错误（KeyError / PermissionError）、schema 解析错误（ValueError / KeyError）—— 这些是 bug 不是 flake
  - idempotent cache hit **永不重试**（asset_prep 是幂等层；Agent 内部判断）
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterable, Type


@dataclass
class RetryPolicy:
    max_attempts: int = 3                       # 含首次尝试
    backoff_seconds: float = 1.0                 # 第一次重试前等 1s，第二次 2s，第三次 4s（指数）
    retry_on: tuple[type[BaseException], ...] = (TimeoutError,)

    def compute_delay(self, attempt: int) -> float:
        """attempt=1 第一次重试前等 1s；attempt=2 等 2s。"""
        return self.backoff_seconds * (2 ** (attempt - 1))


@contextmanager
def with_retry(execute: Callable[[], Any], policy: RetryPolicy):
    """执行 execute()；失败时按 RetryPolicy 重试（仅 retry_on 类型）。

    用法：
        with with_retry(lambda: backend.generate(...), RetryPolicy(max_attempts=3)) as result:
            ...
    """
    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = execute()
            if attempt > 1:
                # 隐式 retry 成功信号留给 caller（不强制）
                pass
            yield result
            return
        except policy.retry_on as exc:
            last_exc = exc
            if attempt >= policy.max_attempts:
                raise
            delay = policy.compute_delay(attempt)
            time.sleep(delay)
        except BaseException:
            # 非 retry_on 类型：直接抛出，不重试
            raise
    # 永远不会到这里（循环要么 return 要么 raise），但保险：
    if last_exc:
        raise last_exc

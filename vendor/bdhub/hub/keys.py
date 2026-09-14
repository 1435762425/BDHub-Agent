# -*- coding: utf-8 -*-
"""handle 归一化：全项目唯一口径(handle_key)。

叶子模块——不导入其他 hub 子模块或业务模块，任何人都能安全依赖它。
"""
from __future__ import annotations


def norm_handle(h: str) -> str:
    h = (h or "").strip()
    if h.startswith("@"):
        h = h[1:]
    return h.lower()

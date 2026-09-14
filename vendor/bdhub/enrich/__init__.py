# -*- coding: utf-8 -*-
"""达人信息库富化引擎。

链路（已实测验证，冷热达人通吃）：
  find(handle) → creator_profile_list[精确匹配] → 88 字段画像 → 缓存 + 入库
入口：python -m bdhub.enrich  [--limit N] [--offset M] [--refresh] [--no-export]
"""

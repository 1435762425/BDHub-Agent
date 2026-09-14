# -*- coding: utf-8 -*-
"""bdhub —— BD 达人中台核心包（012-im / 013-whatsappextra 的下游聚合层）。

子包：
  config            配置加载
  model             领域规范模型（达人画像等）
  enrich/           达人信息库富化引擎（TikTok Partner API: find→画像）
  （后续）sources/  各源适配器（TAP/MCN 报表、IM、WhatsApp）
  （后续）score/    深绑候选分 / 分型
  （后续）report/   全景表 / 名单导出
"""
__version__ = "0.1.0"

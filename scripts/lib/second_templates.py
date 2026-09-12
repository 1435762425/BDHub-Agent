"""Fixed Italy repush wording based on the user's current MX business template.

Only the optional handle and whitelisted product names are interpolated.
Product cards must precede the message; the returned delivery contract records
that dependency. Seasonal hooks, sales recovery and personal contacts are omitted.
No model, network, configuration or database operation is available here.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation

from lib.second_outreach_source import ITALIAN_PRODUCT_NAMES

_ITALIAN_NAMES = dict(ITALIAN_PRODUCT_NAMES)
_CHINESE_NAMES = {
    "1729779362302171335": "Freegrin口腔片",
    "1729480019490150432": "颈枕",
    "1729502070782139035": "瑜伽裤",
    "1729584712875612996": "女士塑身衣",
    "1729480472768911992": "NJTY T3数字万用表",
}
_ARTICLES = {
    "1729779362302171335": "le",
    "1729480019490150432": "il",
    "1729502070782139035": "i",
    "1729584712875612996": "il",
    "1729480472768911992": "il",
}
_PRODUCT_REQUIRED = frozenset({"id", "pid", "title", "nameIt"})
_PRODUCT_ALLOWED = _PRODUCT_REQUIRED | {"units", "windowStart", "windowEnd", "windowBasis", "observedAt", "sourceRef"}
_VERSION = 3
_RENDERER_VERSION = "it-second-fixed-templates-v3"
_SOURCE = "user_mx_second_template_20260912"
_DELIVERY_ORDER = "card_then_text"
_PROMOTION_REASON = "commission_advantage"
_DATIVE_ARTICLES = {"il": "al", "le": "alle", "i": "ai"}
_PROMOTION_FIELDS = {"creatorPercent", "publicPercent", "sourceRef", "observedAt"}
_TEMPLATES = (
    {
        "id": "it-second-brief", "name": "再推一轮 · 标准", "description": "沿用 MX 二发意图，以已核验的佣金优势说明再推一轮的理由；先卡后文字，样品只指向可用入口。",
        "textIt": "Ciao{recipient}! Che ne dici di dare un'altra spinta {productsDativeIt}? 👀 Con il nuovo link BJN la commissione è più alta rispetto al piano pubblico: può valere la pena riprendere il prodotto nel prossimo video o LIVE. Il link è qui sopra: aggiungilo alla tua vetrina e usa quello per il prossimo contenuto. Se ti serve il prodotto per girare, dallo stesso link puoi controllare se hai l'opzione per richiedere un campione.",
        "translationZh": "你好{recipient}！{productsZh}这款可以再推一轮 👀 BJN 新链接的佣金比公开计划更高，值得考虑在下一条视频或 LIVE 里再带一次。链接就在上方，加入橱窗后，下次内容用这个链接。如果需要商品来拍摄，可以从同一链接查看有没有样品申请入口。",
    },
    {
        "id": "it-second-choice", "name": "再推一轮 · 精简", "description": "MX 二发的简短意大利语表达：佣金高于公开计划，邀请再做一条视频或 LIVE，并使用上方 BJN 链接。",
        "textIt": "Ciao{recipient}! Ti va di riproporre {productsIt} nel prossimo video o LIVE? Con il nuovo link BJN la commissione è più alta rispetto al piano pubblico. Il link è qui sopra: aggiungilo alla vetrina e usa quello per il prossimo contenuto. Se ti serve il prodotto per girare, controlla dallo stesso link se c'è l'opzione per richiedere un campione.",
        "translationZh": "你好{recipient}！下一条视频或 LIVE，可以把{productsZh}再推一轮。BJN 新链接的佣金比公开计划更高。链接在上方，加入橱窗后，下次内容用这个链接。如果需要商品来拍摄，可以从同一链接查看有没有样品申请入口。",
    },
    {
        "id": "it-second-explore", "name": "再推一轮 · 视频与 LIVE", "description": "从下一条视频或 LIVE 切入 MX 二发意图，用已核验的佣金优势给出再次推广的理由，不编造销量或时令。",
        "textIt": "Ciao{recipient}! Per il tuo prossimo video o LIVE, perché non riprendere {productsIt}? Il nuovo link BJN offre una commissione più alta rispetto al piano pubblico: un motivo in più per tornare su questo prodotto. Il link è qui sopra: aggiungilo alla tua vetrina e usa quello nel prossimo contenuto. Se ti serve il prodotto per girare, dallo stesso link puoi vedere se è disponibile l'opzione per richiedere un campione.",
        "translationZh": "你好{recipient}！准备下一条视频或 LIVE 时，可以考虑把{productsZh}再推一轮。BJN 新链接的佣金比公开计划更高，这是值得再带一次的理由。链接在上方，加入橱窗后，下次内容用这个链接。如果需要商品来拍摄，可以从同一链接查看有没有样品申请入口。",
    },
)
# Grammatical variants are fixed text, selected solely by the validated SKU count.
# They do not create additional caller-supplied interpolation variables.
_MULTIPLE = {
    "it-second-brief": {
        "textIt": "Ciao{recipient}! Che ne dici di dare un'altra spinta {productsDativeIt}? 👀 I nuovi link BJN offrono commissioni più alte rispetto ai rispettivi piani pubblici: può valere la pena riprendere questi prodotti nel prossimo video o LIVE. I link sono qui sopra: aggiungili alla tua vetrina e usa quelli per i prossimi contenuti. Se ti servono i prodotti per girare, dai rispettivi link puoi controllare se hai l'opzione per richiedere un campione.",
        "translationZh": "你好{recipient}！{productsZh}这几款可以再推一轮 👀 BJN 新链接的佣金比各自的公开计划更高，值得考虑在下一条视频或 LIVE 里再带一次。链接就在上方，加入橱窗后，下次内容用这些链接。如果需要商品来拍摄，可以分别从对应链接查看有没有样品申请入口。",
    },
    "it-second-choice": {
        "textIt": "Ciao{recipient}! Ti va di riproporre {productsIt} nel prossimo video o LIVE? Con i nuovi link BJN le commissioni sono più alte rispetto ai rispettivi piani pubblici. I link sono qui sopra: aggiungili alla vetrina e usa quelli per i prossimi contenuti. Se ti servono i prodotti per girare, controlla dai rispettivi link se c'è l'opzione per richiedere un campione.",
        "translationZh": "你好{recipient}！下一条视频或 LIVE，可以把{productsZh}再推一轮。BJN 新链接的佣金比各自的公开计划更高。链接在上方，加入橱窗后，下次内容用这些链接。如果需要商品来拍摄，可以分别从对应链接查看有没有样品申请入口。",
    },
    "it-second-explore": {
        "textIt": "Ciao{recipient}! Per il tuo prossimo video o LIVE, perché non riprendere {productsIt}? I nuovi link BJN offrono commissioni più alte rispetto ai rispettivi piani pubblici: un motivo in più per tornare su questi prodotti. I link sono qui sopra: aggiungili alla tua vetrina e usa quelli nei prossimi contenuti. Se ti servono i prodotti per girare, dai rispettivi link puoi vedere se è disponibile l'opzione per richiedere un campione.",
        "translationZh": "你好{recipient}！准备下一条视频或 LIVE 时，可以考虑把{productsZh}再推一轮。BJN 新链接的佣金比各自的公开计划更高，这是值得再带一次的理由。链接在上方，加入橱窗后，下次内容用这些链接。如果需要商品来拍摄，可以分别从对应链接查看有没有样品申请入口。",
    },
}


class SecondTemplateError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(template):
    return hashlib.sha256(_json({"template": template, "version": _VERSION, "rendererVersion": _RENDERER_VERSION,
                                "italianNames": _ITALIAN_NAMES, "chineseNames": _CHINESE_NAMES, "articles": _ARTICLES,
                                "dativeArticles": _DATIVE_ARTICLES, "promotionReason": _PROMOTION_REASON,
                                "multiple": _MULTIPLE[template["id"]], "source": _SOURCE, "requiresCard": True, "deliveryOrder": _DELIVERY_ORDER}).encode("utf-8")).hexdigest()


def list_templates():
    """Return fresh public template definitions; caller mutation cannot alter them."""
    return [{**template, "version": _VERSION, "fingerprint": _fingerprint(template),
             "requiresCard": True, "deliveryOrder": _DELIVERY_ORDER, "source": _SOURCE,
             "promotionReason": _PROMOTION_REASON} for template in _TEMPLATES]


def _recipient(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise SecondTemplateError("template_recipient_invalid")
    handle = value.strip().removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,64}", handle):
        raise SecondTemplateError("template_recipient_invalid")
    return handle


def _products(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise SecondTemplateError("template_product_count_invalid")
    pids, ids, result = set(), set(), []
    for product in value:
        if not isinstance(product, dict) or not _PRODUCT_REQUIRED <= set(product) or set(product) - _PRODUCT_ALLOWED:
            raise SecondTemplateError("template_product_fields_invalid")
        pid = product["pid"]
        if not isinstance(pid, str) or pid not in _ITALIAN_NAMES or pid not in _CHINESE_NAMES or product["nameIt"] != _ITALIAN_NAMES[pid]:
            raise SecondTemplateError("template_product_name_unverified")
        product_id, title = product["id"], product["title"]
        if (not isinstance(product_id, str) or not product_id.strip() or len(product_id) > 100
                or not isinstance(title, str) or not title.strip() or len(title) > 300
                or any(ord(char) < 32 for char in product_id + title)):
            raise SecondTemplateError("template_product_fields_invalid")
        if pid in pids or product_id in ids:
            raise SecondTemplateError("template_duplicate_product")
        pids.add(pid)
        ids.add(product_id)
        result.append({"pid": pid, "nameIt": _ITALIAN_NAMES[pid], "nameZh": _CHINESE_NAMES[pid]})
    return result


def _join_italian(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


def _join_chinese(names):
    return names[0] if len(names) == 1 else "、".join(names[:-1]) + "和" + names[-1]


def _promotion_claims(selected, promotion_facts):
    """Evaluate only the selected PIDs; unavailable facts never become zero rates."""
    try:
        if not isinstance(promotion_facts, Mapping):
            raise ValueError()
        claims = []
        for product in selected:
            fact = promotion_facts.get(product["pid"])
            if not isinstance(fact, Mapping) or set(fact) != _PROMOTION_FIELDS:
                raise ValueError()
            numbers = []
            for key in ("creatorPercent", "publicPercent"):
                value = fact[key]
                if not isinstance(value, str) or len(value) > 80 or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None:
                    raise ValueError()
                number = Decimal(value)
                if not number.is_finite() or not 0 <= number <= 100:
                    raise ValueError()
                numbers.append(number)
            if numbers[0] <= numbers[1]:
                raise ValueError()
            source = fact["sourceRef"]
            if not isinstance(source, str) or not source.strip() or len(source) > 512 or any(ord(char) < 32 for char in source):
                raise ValueError()
            source.encode("utf-8")
            observed = fact["observedAt"]
            if not isinstance(observed, str) or len(observed) > 64:
                raise ValueError()
            parsed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError()
            claims.append({"kind": "commission_above_public", "pid": product["pid"],
                           **{key: fact[key] for key in ("creatorPercent", "publicPercent", "sourceRef", "observedAt")}})
        return claims
    except (ValueError, TypeError, InvalidOperation, UnicodeError):
        raise SecondTemplateError("promotion_reason_missing") from None


def render_template(template_id, recipient_handle, products, *, promotion_facts=None):
    if not isinstance(template_id, str):
        raise SecondTemplateError("template_unknown")
    template = next((item for item in _TEMPLATES if item["id"] == template_id), None)
    if template is None:
        raise SecondTemplateError("template_unknown")
    handle, selected = _recipient(recipient_handle), _products(products)
    claims = _promotion_claims(selected, promotion_facts)
    names_it = [item["nameIt"] for item in selected]
    names_zh = [item["nameZh"] for item in selected]
    grammatical_names = [_ARTICLES[item["pid"]] + " " + item["nameIt"] for item in selected]
    dative_names = [_DATIVE_ARTICLES[_ARTICLES[item["pid"]]] + " " + item["nameIt"] for item in selected]
    wording = template if len(selected) == 1 else _MULTIPLE[template["id"]]
    text_it = wording["textIt"].format(recipient=" @" + handle if handle else "", productsIt=_join_italian(grammatical_names), productsDativeIt=_join_italian(dative_names))
    text_zh = wording["translationZh"].format(recipient="，@" + handle if handle else "", productsZh=_join_chinese(names_zh))
    if not 1 <= len(text_it) <= 900 or not 1 <= len(text_zh) <= 900:
        raise SecondTemplateError("template_text_too_long")
    return {"templateId": template["id"], "templateName": template["name"], "templateVersion": _VERSION,
            "templateFingerprint": _fingerprint(template), "textIt": text_it, "translationZh": text_zh,
            "variables": {"recipientHandle": handle, "productNamesIt": names_it, "productNamesZh": names_zh},
            "textSha256": hashlib.sha256(text_it.encode("utf-8")).hexdigest(),
            "requiresCard": True, "requiredCardPids": [item["pid"] for item in selected],
            "deliveryOrder": _DELIVERY_ORDER, "source": _SOURCE,
            "promotionReason": _PROMOTION_REASON, "promotionClaims": claims}


__all__ = ["SecondTemplateError", "list_templates", "render_template"]

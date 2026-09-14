"""跨页建链选择：冻结当前来源的合格 PID，已有记录不再选中。"""
from .commerce_selection import best_offer
from .commerce_sources import source_products
from .commerce_pool import sales_value
from .catalog_rules import filters_for
from bdhub.send.taplink.rule_batches import existing_index

MAX_BUILD_ITEMS = 10000


def build_choices(market, account, source, search='', *, pool=None, known=None, rules=None):
    if source not in {'campaign', 'selected'}:
        raise ValueError('commerce_selected_source_required')
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('commerce_selection_scope_invalid')
    rules = filters_for(market) if rules is None else rules
    known = existing_index(market, account) if known is None else known
    products = source_products(market, account, source, store=pool)
    products.sort(key=lambda p: (-(sales_value(p.get('sales')) or 0), p['product_id']))
    choices = []; eligible = existing = 0
    for product in products:
        if search.casefold() not in f"{product['product_id']} {product.get('title', '')} {product.get('shop_name', '')}".casefold():
            continue
        offer = best_offer(product, rules)
        if not offer:
            continue
        eligible += 1
        if product['product_id'] in known:
            existing += 1
            continue
        choices.append({'pid':product['product_id'], 'scan_id':product['scan_id'], 'offer_key':offer['offer_key']})
    if len(choices) > MAX_BUILD_ITEMS:
        raise ValueError('commerce_build_limit_exceeded')
    return {'choices':choices, 'total':len(choices), 'eligible_count':eligible, 'existing_count':existing}

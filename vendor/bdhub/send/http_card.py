"""HTTP商品卡协议实验；不发网络请求，尚未接入正式批量发送。"""
from dataclasses import dataclass
import json

from bdhub.send.http_protocol import build_fresh_send,decode_envelope,history_checks,wire_fields,one
from bdhub.send.transport_scripts import _CARD_TITLE_KEY

CARD_CONTENT='[商品列表]'


def select_product_card(payload,pid,*,list_id=None):
    if not isinstance(payload,dict) or type(payload.get('code')) is not int or payload['code']!=0:
        raise ValueError('http_card_lookup_business_error')
    lists=(payload.get('data') or {}).get('list')
    if not isinstance(lists,list):raise ValueError('http_card_lookup_malformed')
    candidates={}
    for item in lists:
        if not isinstance(item,dict):continue
        products=[p for p in item.get('campaign_products',[]) if isinstance(p,dict) and str(p.get('product_id',p.get('productId','')))==pid]
        if not products:continue
        lid=str(item.get('product_list_id') or '')
        if list_id is not None and lid!=list_id:continue
        product=products[0]
        stock=product.get('stock')
        if stock is not None:
            try:
                if int(stock)<=0:continue
            except (TypeError,ValueError):raise ValueError('http_card_stock_invalid') from None
        card=ProductCard(pid,lid,str(item.get('campaign_id') or '0'),str(item.get('campaign_name') or ''),str(item.get('product_list_name') or ''))
        candidates[(card.list_id,card.campaign_id)]=(card,product)
    if len(candidates)!=1:raise ValueError('http_card_lookup_not_unique' if candidates else 'http_card_product_unavailable')
    return next(iter(candidates.values()))


def lookup_product_card(account,pid,*,expected=None):
    """只读现有商品货盘，按专用商品identity与精确PID绑定；不建链、不新增货盘。"""
    import requests
    from bdhub import config,scheduled_relogin
    from bdhub.hub.markets import identity_for
    from bdhub.enrich.identity_store import load_identity
    if scheduled_relogin.maintenance_due(account,initialize=False,ignore_retry_throttle=True):raise ValueError('http_card_maintenance_due')
    identity=identity_for('mx',account=account,cfg=config.load()).require_product_search()
    if not identity.partner_id_is_own:raise ValueError('http_card_identity_not_own')
    headers=load_identity(account.headers_json).headers
    headers={k:v for k,v in headers.items() if not k.startswith(':') and k.lower() not in ('host','content-length')}
    with requests.Session() as session:
        session.trust_env=False
        response=session.get(identity.host+'/api/v1/affiliate/partner/im/product_list/list',headers=headers,
            params={'cur_page':1,'page_size':10,'version':1,'search_type':2,'key_word':pid,'user_language':'zh-CN',
                'partner_id':identity.im_market_partner_id,'aid':identity.aid,'app_name':'i18n_ecom_alliance','device_platform':'web'},
            timeout=(5,15),allow_redirects=False)
        if response.status_code!=200 or response.headers.get('bdturing-verify'):raise ValueError('http_card_lookup_http_error')
        card,product=select_product_card(response.json(),pid,list_id=expected.list_id if expected else None)
        if expected and card!=expected:raise ValueError('http_card_binding_changed')
        from bdhub.send.taplink.cleanup import CleanupStore
        if CleanupStore().is_blocked('mx',card.list_id):raise ValueError('http_card_retired')
        return card,product


@dataclass(frozen=True,slots=True)
class ProductCard:
    product_id:str
    list_id:str
    campaign_id:str='0'
    campaign_name:str=''
    list_name:str=''
    title_key:str=_CARD_TITLE_KEY

    def __post_init__(self):
        for name in ('product_id','list_id','campaign_id'):
            value=getattr(self,name)
            if not isinstance(value,str) or not value.isascii() or not value.isdigit():
                raise ValueError('http_card_invalid_'+name)
            if name!='campaign_id' and int(value)<=0:raise ValueError('http_card_invalid_'+name)
        if self.title_key!=_CARD_TITLE_KEY:raise ValueError('http_card_title_key_unsupported')


def build_card_packet(auth,conversation,card,*,handle,sequence,client_id):
    ext={**auth.message_ext,'PIGEON_BIZ_TYPE':'1','type':'product_list','starling_content_key':card.title_key,
         'product_id':card.product_id,'list_id':card.list_id,'campaign_id':card.campaign_id,
         'campaign_name':card.campaign_name,'list_name':card.list_name,
         'b:oec_im_search_context':json.dumps({'sender_name':handle,'search_content_map':{'name':handle}},separators=(',',':'))}
    return build_fresh_send(auth,conversation,text=CARD_CONTENT,sequence=sequence,client_id=client_id,ext=ext)


def verify_card_readback(data,packet,server_id,*,sequence):
    if packet.message_ext.get('type')!='product_list' or packet.text!=CARD_CONTENT.encode():
        raise ValueError('http_card_expected_packet_invalid')
    checks=history_checks(data,packet,int(server_id),sequence=sequence)
    required=('server_id_matches','full_cid_match','short_cid_match','content_match','sender_match','client_id_match','text_type_match')
    if not all(checks.get(k) is True for k in required):raise ValueError('http_card_message_mismatch')
    body=wire_fields(one(decode_envelope(data,cmd=301,sequence=sequence),301,b''))
    for raw in body.get(1,[]):
        msg=wire_fields(raw)
        if one(msg,3)!=int(server_id):continue
        ext={one(wire_fields(v),1):one(wire_fields(v),2) for v in msg.get(9,[])}
        if ext.get(b's:visible') or ext.get(b'visibility_type') not in (None,b'',b'0'):
            raise ValueError('http_card_restricted_visibility')
        for field in ('type','product_id','list_id','campaign_id','starling_content_key'):
            if ext.get(field.encode())!=packet.message_ext[field].encode():
                raise ValueError('http_card_'+field+'_mismatch')
        return {**checks,'card_metadata_match':True,'restricted_visibility':False}
    raise ValueError('http_card_message_missing')

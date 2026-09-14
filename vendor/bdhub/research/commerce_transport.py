"""非 MX 商品工作区协议；保留账号身份、租约和单次写入语义。"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
import time
from urllib.parse import urlsplit

from bdhub.enrich.profile_lease import ProfileLease
from bdhub.hub.markets import identity_for, require_capability
from bdhub.imbase.account_binding import resolve_bound_profile
from bdhub.research.product_source_transport import SourceTransport, seller_join_payload, SELLER_JOIN_PATH
from bdhub.send.sharelink.transport import SEARCH_PATH, OPPORTUNITY_SEARCH_PATH, OPPORTUNITY_DETAIL_PATH, PICK_UP_SELECT_PATH
from bdhub.send.taplink.protocol import CREATE_PATH, CARD_LIST_PATH, identifier, find_card
from bdhub.send.taplink.transport import account_for

MARKETS = frozenset({'br', 'it', 'uk', 'us', 'jp', 'de'})
REVIEW_PATH = '/api/v1/affiliate/partner/campaign/review'
PRODUCTS_PATH = '/api/v1/affiliate/partner/campaign/product_list/products'
CATEGORY_PATH = '/api/v1/affiliate/lux/product/category/childrenv2'


def require_market(market):
    if market not in MARKETS:
        raise ValueError('commerce_market_invalid')


class CommerceTransport(SourceTransport):
    WRITE_ENDPOINTS = SourceTransport.WRITE_ENDPOINTS | {(CREATE_PATH, 'POST'), (REVIEW_PATH, 'POST')}
    READ_ENDPOINTS = SourceTransport.READ_ENDPOINTS | {(PRODUCTS_PATH, 'GET'), (CATEGORY_PATH, 'POST')}

    def __init__(self, identity, account, **kwargs):
        require_market(identity.market)
        super().__init__(identity, account, **kwargs)
        self._account=account
        host = urlsplit(identity.home)
        origin = f'{host.scheme}://{host.netloc}'
        self.headers.update(origin=origin, referer=origin + '/')
        self.verification_attempts = 0
        self.verification_successes = 0
        self.read_retry_count = 0
        self._verification_header = ''
        self._pending_selection_verification = None
        self._verification_failed = False
        self._verification_seen = set()
        self.session.hooks['response'].append(self._capture_verification)

    def copy_session_from(self,source):
        if (self._account.name!=source._account.name or self.identity.market!=source.identity.market
                or self.identity.partner_id!=source.identity.partner_id or self.device_id!=source.device_id):
            raise ValueError('commerce_session_scope_invalid')
        self.headers=dict(source.headers);self.fp=source.fp
        self.session.cookies.clear();self.session.cookies.update(source.session.cookies.get_dict())
        self._pending_selection_verification=None
        self._verification_seen.update(source._verification_seen)
        self._verification_failed=source._verification_failed

    def selection_lane(self,pace):
        if self.identity.market!='it' or not self.allow_write:raise ValueError('commerce_selection_lane_scope_invalid')
        lane=CommerceTransport(self.identity,self._account,allow_write=True)
        lane.WRITE_ENDPOINTS={(PICK_UP_SELECT_PATH,'POST')}
        lane.copy_session_from(self);lane._pace=pace
        return lane

    def _capture_verification(self, response, **_kwargs):
        self._verification_header = response.headers.get('bdturing-verify', '')

    def _solve_verification(self, header):
        """每个真实挑战只处理一次；复用已有同账号HTTP验证模块。"""
        token=hashlib.sha256(header.encode()).hexdigest()
        seen=getattr(self,'_verification_seen',set())
        if getattr(self,'_verification_failed',False) or token in seen:
            raise ValueError('commerce_verification_required')
        seen.add(token);self._verification_seen=seen
        self.verification_attempts+=1
        from .commerce_verification import verify_session
        try:verify_session(self,header)
        except Exception:
            self._verification_failed=True
            raise

    def resolve_selection_verification(self,pid,campaign_id):
        """只处理刚才选入请求的验证，不回放选入POST；结果由上层只读回查。"""
        pending=getattr(self,'_pending_selection_verification',None)
        if (self.identity.market!='it' or not self.allow_write or not pending
                or pending['pid']!=str(pid) or pending['campaign_id']!=str(campaign_id)):
            raise ValueError('commerce_selection_verification_scope_invalid')
        self._pending_selection_verification=None
        self._solve_verification(pending['header'])
        self.verification_successes+=1
        return True

    def category_children(self, parent='0'):
        import re
        if not re.fullmatch(r'[0-9]{1,20}', str(parent)):
            raise ValueError('commerce_category_invalid')
        region = 'GB' if self.identity.market == 'uk' else self.identity.market.upper()
        body = self.require_read(self._xhr(method='POST', path=CATEGORY_PATH, params=self._params(),
            payload={'category_id':str(parent), 'status_param':{'region':region}}, write=False))
        rows = (body.get('data') or {}).get('category_infos')
        if not isinstance(rows, list):
            raise ValueError('commerce_category_response_invalid')
        if any(not isinstance(r, dict) or not re.fullmatch(r'[0-9]{1,20}', str(r.get('category_id')))
               or not isinstance(r.get('name'), str) or type(r.get('is_leaf')) is not bool for r in rows):
            raise ValueError('commerce_category_response_invalid')
        return [{'category_id':str(r['category_id']), 'name':r['name'], 'is_leaf':r['is_leaf']} for r in rows]

    def _xhr(self, **kwargs):
        self._verification_header = ''
        selecting=kwargs.get('write') is True and kwargs.get('method')=='POST' and kwargs.get('path')==PICK_UP_SELECT_PATH
        if selecting:self._pending_selection_verification=None
        result = super()._xhr(**kwargs)
        # 仅在内存保留选入挑战；批量器先保存意图，再显式处理验证和回查。
        if (selecting and self.identity.market=='it' and result.http_status==200 and type(result.code) is int and result.code==10000
                and result.has_turing and not result.ambiguous and self._verification_header):
            body=kwargs.get('payload') or {}
            self._pending_selection_verification={'pid':str(body.get('product_id','')),'campaign_id':str(body.get('campaign_id','')),'header':self._verification_header}
        # 只读请求可以在验证后回放；任何平台写请求都不会在这里重发。
        if result.has_turing and not kwargs.get('write') and self.identity.market == 'it' and self._verification_header:
            self._solve_verification(self._verification_header)
            kwargs = kwargs | {'params':kwargs['params'] | {'fp':self.fp}}
            result = super()._xhr(**kwargs)
            if result.has_turing or result.system_error_3 or result.code != 0:
                raise ValueError('commerce_verification_required')
            self.verification_successes += 1
        # 意大利已实测：精确搜索零结果只返回 data.total=0，省略 list。
        if kwargs.get('path') == CARD_LIST_PATH and not kwargs.get('write'):
            data = result.payload.get('data')
            if isinstance(data, dict) and type(data.get('total')) is int and data['total'] == 0 and 'list' not in data:
                data['list'] = []
        return result

    def opportunity_page(self, page, *, global_only=False, pids=None, category_id=None):
        payload = {'filter': {'product_source': [], 'campaign_type': [8] if global_only else [],
                              'label_type': [], 'product_status': 1}, 'page': page, 'page_size': 15}
        if pids:
            payload['product_id'] = pids
        if category_id:
            payload['filter']['category_id']=[str(category_id)]
        for attempt in range(3):
            result=self._xhr(method='POST',path=OPPORTUNITY_SEARCH_PATH,params=self._params(),payload=payload,write=False)
            self.last_read={'page':page,'http':result.http_status,'code':result.code if type(result.code) is int else 'non_numeric',
                'verification':result.has_turing,'system_error':result.system_error_3}
            # 仅网络中断/服务器暂时错误有界重读原页；限流、认证、验证及业务拒绝不重试。
            transient=result.http_status in {0,500,502,503,504} or (result.code==100000 and result.system_error_3)
            if not transient or result.has_turing or attempt==2:break
            self.read_retry_count=getattr(self,'read_retry_count',0)+1
            self.last_retry=dict(self.last_read)
            time.sleep(2*(attempt+1))
        data=self.require_read(result).get('data')
        if pids and isinstance(data,dict) and type(data.get('total')) is int and data['total']==0 and data.get('has_more') is False and data.get('products') is None:
            data=data|{'products':[]}
        if not isinstance(data, dict) or not isinstance(data.get('products'), list) or type(data.get('has_more')) is not bool:
            raise ValueError('commerce_pagination_incomplete')
        return data

    def offers(self, pid):
        data = self.require_read(self._xhr(method='GET', path=OPPORTUNITY_DETAIL_PATH,
                    params=self._params() | {'product_id': pid}, payload=None, write=False)).get('data')
        if not isinstance(data, dict) or not isinstance(data.get('product_campaign_detail'), list):
            raise ValueError('commerce_offer_response_invalid')
        return data['product_campaign_detail']

    def selected_page(self, page, *, pids=None):
        body={'cur_page':page,'page_size':100,'filter':{'product_source':[], 'campaign_type':[],
            'label_type':[], 'product_status':1}}
        if pids:
            body['product_ids']=pids
        result=self.require_read(self._xhr(method='POST',path=SEARCH_PATH,params=self._params(),payload=body,write=False))
        rows=result.get('data')
        total=result.get('total_num')
        if total==0 and rows is None:
            rows=[]
        if not isinstance(rows,list) or isinstance(total,bool) or not isinstance(total,(int,str)) or int(total)<0:
            raise ValueError('commerce_selected_response_invalid')
        return {'items':rows,'total':int(total)}

    def join_offer(self, cid, campaign_type, email):
        if campaign_type == '4':
            path, payload = SELLER_JOIN_PATH, seller_join_payload(cid, email)
        elif campaign_type == '6':
            path, payload = REVIEW_PATH, {'campaign_id': cid, 'is_joined': True}
        else:
            raise ValueError('commerce_join_type_invalid')
        result = self._xhr(method='POST', path=path, params=self._params(), payload=payload, write=True)
        # 回执缺失不能假定加入失败，更不能重新提交。
        body = self.require_read(result)
        return identifier(str((body.get('data') or {}).get('campaign_id') or ''))

    def selected_after(self, item):
        exact = self.selected(item['pid'], item['campaign_id'])
        if exact or item['offer']['campaign_type'] != '8':
            return exact
        # 全球池选择后从 8 转为账号子活动 9；用精确 PID、唯一选中方案及全部活动事实核对。
        original = item['raw']['campaign_info']
        matches = []
        for candidate in self.offers(item['pid']):
            campaign = candidate.get('campaign') or {}
            if (candidate.get('is_selected') is True and str(campaign.get('crs_campaign_type')) == '9'
                    and all(str(campaign.get(k)) == str(original.get(k)) for k in ('name', 'promotion_start_time', 'promotion_end_time', 'commission'))
                    and str(candidate.get('open_collab_rate')) == str(item['raw']['campaign_product'].get('plan_commission_percent'))):
                found = self.selected(item['pid'], str(campaign.get('campaign_id')))
                if found:
                    matches.append(found)
        if len(matches) > 1:
            raise ValueError('commerce_global_mapping_ambiguous')
        return matches[0] if matches else None

    def card(self, job):
        if not job.get('unified'):
            return super().card(job)
        from bdhub.send.taplink.cleanup import CleanupTransport
        pid, cid = job['offer']['pid'], job['offer']['campaign_id']
        expected = job.get('receipt', {}).get('list_id')
        # 创建响应没有编号时，不以相似名称猜测已有列表。
        if not expected:
            return None
        from bdhub.send.taplink.service import validate_live
        raw, _ = self.offer(job)
        validate_live(job, raw)
        for page in range(1, 21):
            body = self.require_read(self._xhr(method='GET', path=CARD_LIST_PATH, params=self._params() | {
                'cur_page': page, 'page_size': 20, 'version': 1, 'search_type': 2, 'key_word': pid}, payload=None, write=False))
            rows = (body.get('data') or {}).get('list')
            if not isinstance(rows, list):
                raise ValueError('commerce_card_read_incomplete')
            for row in rows:
                if str(row.get('product_list_id')) != expected or row.get('product_list_name') != job['list_name']:
                    continue
                products = CleanupTransport.products(self, {'list_id': expected, 'source': 2 if job['route'] == 'selected' else 1, 'campaign_id': cid})
                members = [p for p in products if str(p.get('product_id')) == pid and str(p.get('campaign_id')) == cid]
                if len(members) != 1:
                    return None
                member = members[0]
                if (str(member.get('product_status')) != '2' or Decimal(str(member.get('stock') or 0)) <= 0
                        or member.get('is_under_governed') is True or str(member.get('unavailable_type') or '0') not in {'0', '0.0'}
                        or Decimal(str(member.get('creator_commission_percent') or -1)) != Decimal(job['creator_commission']) * 100
                        or Decimal(str(member.get('total_commission_percent') or -1)) != Decimal(job['offer']['total_commission']) * 100
                        or Decimal(str(member.get('plan_commission_percent') or -1)) != Decimal(job['offer']['public_commission']) * 100):
                    return None
                verified_row = deepcopy(row)
                for p in verified_row.get('campaign_products', []):
                    if str(p.get('product_id')) == pid:
                        if p.get('campaign_id') and str(p['campaign_id']) != cid:
                            return None
                        # IM 列表省略活动 ID；同一 list_id 的完整商品成员接口提供这个绑定。
                        p['campaign_id'] = cid
                card = find_card({'code': 0, 'data': {'list': [verified_row]}}, pid=pid, campaign_id=cid,
                    route=job['route'], name=job['list_name'], list_id=expected, strict=True)
                if card:
                    card['binding_evidence'] = 'im_list_and_product_list_membership'
                return card
            if len(rows) < 20:
                return None
        raise ValueError('commerce_card_page_limit')


@contextmanager
def commerce_transport(job, *, allow_write=False, validation=False):
    require_market(job['market'])
    if allow_write:
        if validation:
            from .commerce_links import validate_canary
            validate_canary(job)
        else:
            for capability in (('product_select',) if job.get('action')=='select' else ('tap_link',) + (('product_select',) if any(i['route']=='selected' for i in job.get('items',[])) else ())):
                require_capability(job['market'], capability)
    cfg, account = account_for(job['market'], job['account'])
    identity = identity_for(job['market'], account=account, cfg=cfg).require_product_search()
    if not identity.partner_id_is_own:
        raise ValueError('commerce_identity_not_own')
    with ProfileLease(resolve_bound_profile(account), account=account.name, market=job['market'], operation='commerce_workspace'):
        transport = CommerceTransport(identity, account, allow_write=allow_write)
        try:
            yield transport
        finally:
            transport.session.close()

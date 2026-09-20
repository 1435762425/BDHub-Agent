"""写入作用域构造函数：把"只在真写入时才会跑到"的那几行也跑一遍。

为什么需要这个文件：`opportunity_campaign_joiner` 里用了 `CAMPAIGNS` 而模块从未定义它，
于是一个 `NameError` 一直潜伏到用户第一次点「一键加入」才炸——**测试里所有写入路径都是注入
假传输的，真实的 scope 构造函数一行都没被执行过**。这里把 `_opportunity_transport` 打桩，
让每个构造函数真正跑完自己的校验、冻结和「允许的端点」那几行：未定义的名字会当场失败，
不必等到动平台的那一刻。**不连网、不写任何东西**（打桩的传输不做请求）。
"""
import contextlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'vendor'))
from lib import global_source_transport as transport  # noqa: E402

PID = '1729480061238089885'
LIST_ID = '8650756273145355030'
CAMPAIGN = '7666370360582260502'


class Stub:
    """Stand-in for the real transport: records how it was opened, never does I/O."""

    def __init__(self):
        self.opened = None

    def _xhr(self, **kwargs):
        raise AssertionError('打桩的传输不该被真的请求')


@contextlib.contextmanager
def patched():
    stub = Stub()
    calls = {}
    real = transport._opportunity_transport

    def fake(report, **kwargs):
        calls.update(kwargs)
        stub.opened = kwargs
        return _yield(stub)

    transport._opportunity_transport = fake
    try:
        yield stub, calls
    finally:
        transport._opportunity_transport = real


@contextlib.contextmanager
def _yield(stub):
    yield stub


class Scopes(unittest.TestCase):
    def setUp(self):
        self.report = {}

    def enter(self, build):
        """Run one scope builder; a NameError/undefined name fails right here."""
        with patched() as (stub, calls):
            with build() as live:
                self.assertIs(live, stub)
        return calls

    def test_campaign_join_scope_freezes_payloads_and_allows_the_campaign_list_read(self):
        payload = {'campaign_id': CAMPAIGN, 'contact_info': {'campaign_contact_info': []}}
        calls = self.enter(lambda: transport.opportunity_campaign_joiner(self.report, {CAMPAIGN: dict(payload)}))
        self.assertEqual(calls['campaign_scope'], {CAMPAIGN: payload})
        self.assertEqual(calls['account_name'], 'acc9')
        # 加入之前要读活动列表判断还能不能加入：这个端点必须被允许，用的就是 CAMPAIGNS 常量。
        self.assertIn((transport.CAMPAIGNS, 'GET'), calls['extra_read_endpoints'])

    def test_campaign_join_scope_refuses_a_payload_for_another_campaign(self):
        with patched():
            with self.assertRaises(ValueError) as caught:
                with transport.opportunity_campaign_joiner(self.report, {CAMPAIGN: {'campaign_id': '1' * 19}}):
                    pass
        self.assertEqual(str(caught.exception), 'campaign_join_scope_invalid')

    def test_every_write_scope_builder_reaches_its_transport(self):
        """五个构造函数都要真的跑一遍：它们的校验与冻结逻辑只在真写入时才轮到。"""
        cases = {
            'selector': lambda: transport.opportunity_selector(self.report, {'pids': [PID]}),
            'creator': lambda: transport.opportunity_card_creator(self.report, {'items': [{'product_id': PID}]}),
            'creator_batch': lambda: transport.opportunity_card_creator_batch(self.report, {PID: {'items': [{'product_id': PID}]}}),
            'deleter_batch': lambda: transport.opportunity_list_deleter_batch(self.report, [LIST_ID]),
            'campaign_joiner': lambda: transport.opportunity_campaign_joiner(self.report, {CAMPAIGN: {'campaign_id': CAMPAIGN}}),
        }
        for name, build in cases.items():
            with self.subTest(scope=name):
                calls = self.enter(build)
                self.assertEqual(calls['account_name'], 'acc9')

    def test_every_declared_write_scope_really_enables_writes(self):
        """`allow_write` 必须由**所有**写入作用域推导，一条都不能漏。

        漏掉一条，那条路径就会在**发出请求之前**被旧版 `TapLinkError('taplink_endpoint_not_allowed')`
        挡下——加入活动就是这么失败的；而且写入计数发生在发送之后，账本上还显示"平台写入 0"，
        从记录里根本看不出根因。这个推导刻意收在一个函数里，就是为了不再分散写。
        """
        scopes = ('selection_scope', 'creation_scope', 'deletion_scope', 'campaign_scope')
        self.assertFalse(transport.write_enabled())
        for name in scopes:
            self.assertTrue(transport.write_enabled(**{name: {'x': 1}}), name)
        # 传输层两处（建传输、fork_lane）都必须用这一个推导，不能各自手写一份。
        source = (ROOT / 'scripts/lib/global_source_transport.py').read_text(encoding='utf-8')
        self.assertEqual(source.count('allow_write=write_enabled('), 1)
        self.assertEqual(source.count('allow_write=allow_write'), 2)

    def test_extra_reads_are_merged_and_must_be_on_the_allowlist(self):
        """创建批次可以多声明只读端点，但**必须**在允许集合里。

        踩过两次：少声明一次读，整条写入路径就在发出请求之前被
        TapLinkError('taplink_endpoint_not_allowed') 挡下，而且因为写入计数在发送之后，
        账本上还显示"平台写入 0"，从记录里看不出根因。
        """
        payloads = {PID: {'items': [{'product_id': PID}]}}
        with patched() as (_, calls):
            with transport.opportunity_card_creator_batch(self.report, payloads,
                                                          extra_reads=transport.CAMPAIGN_OFFER_READS):
                pass
        self.assertIn((transport.CAMPAIGNS, 'GET'), calls['extra_read_endpoints'])
        self.assertIn(('/api/v1/affiliate/partner/campaign/product/list', 'GET'), calls['extra_read_endpoints'])
        # 非全托建链要读的那两个端点必须在允许集合里，否则声明了也会被拒。
        self.assertTrue(transport.CAMPAIGN_OFFER_READS <= transport.ALLOWED_EXTRA_READS)

    def test_the_allowed_read_endpoints_are_all_real_constants(self):
        """允许的只读端点必须是模块里定义好的常量值，不能是漏定义的变量（那正是这次的错）。"""
        with patched() as (_, calls):
            for build in (lambda: transport.opportunity_card_creator_batch(self.report, {PID: {'items': [{'product_id': PID}]}}),
                          lambda: transport.opportunity_list_deleter_batch(self.report, [LIST_ID])):
                with build():
                    pass
        for endpoint in ('/api/v1/affiliate/partner/campaign/list',
                         '/api/v1/affiliate/partner/campaign/product_list/list'):
            self.assertIn(endpoint, (transport.CAMPAIGNS, transport.LIST_INVENTORY))


if __name__ == '__main__':
    unittest.main()

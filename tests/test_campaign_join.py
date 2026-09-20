"""加入活动：确认门、写入前落盘、结果未知不重试、回查结算。全程用假传输，不碰平台。"""
import contextlib
import json
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.campaign_join import (Store, apply, default_email, eligibility, join_all,  # noqa: E402
                               join_payload, preview, read_other_categories, status, verify)

NOW = 1789257600
FAR = int((datetime.fromtimestamp(NOW, timezone.utc) + timedelta(days=200)).timestamp() * 1000)
SOON = int((datetime.fromtimestamp(NOW, timezone.utc) + timedelta(days=10)).timestamp() * 1000)
# 活动 id 是 10–32 位数字（协议校验，旧版同样），所以例子用真实的 19 位。
A, B, C, D, E = '1' * 19, '2' * 19, '3' * 19, '9' * 19, '7' * 19


def outcome(*, http=200, code=0, ambiguous=False, turing=False, returned=None):
    payload = {'data': {'campaign_id': returned}} if returned else {'data': {}}
    return type('Outcome', (), {'http_status': http, 'code': code, 'ambiguous': ambiguous,
                               'has_turing': turing, 'payload': payload})()


class Fake:
    """A read+write transport stand-in: answers the two lists and records every write."""

    def __init__(self, *, joinable=None, joined=None, answer=None):
        self.joinable = [{'campaign_id': cid, 'name': f'c{cid}', 'promotion_end_time': FAR}
                         for cid in (joinable or [])]
        self.joined_rows = [{'campaign_id': cid} for cid in (joined or [])]
        self.answer = answer or (lambda cid, payload: outcome())
        self.writes = []
        self.page_size = 100
        self.closed = False

    def _params(self):
        return {}

    def _xhr(self, *, method, path, params=None, payload=None, write=False):
        if write:
            self.writes.append((path, dict(payload or {})))
            return self.answer(str((payload or {}).get('campaign_id')), dict(payload or {}))
        which = self.joinable if params.get('status') == 0 else self.joined_rows
        page = int((params or {}).get('cur_page') or 1)
        start = (page - 1) * self.page_size
        return type('Outcome', (), {'http_status': 200, 'code': 0,
                                    'payload': {'data': {'campaign': which[start:start + self.page_size],
                                                         'total': len(which)}}})()

    def require_read(self, outcome):
        return outcome.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


@contextlib.contextmanager
def context(fake):
    yield fake


class Preview(unittest.TestCase):
    def test_it_separates_joinable_eligible_joined_and_too_short(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A, B], joined=[B])
            fake.joinable.append({'campaign_id': C, 'name': 'soon', 'promotion_end_time': SOON})
            result = preview(folder, transport=context(fake), clock=lambda: NOW, job_id='job-1')
            by_id = {row['campaign_id']: row for row in result['items']}
            self.assertEqual(by_id[A]['state'], 'eligible')
            self.assertEqual(by_id[B]['state'], 'joined')
            self.assertEqual(by_id[C]['state'], 'skipped')
            self.assertEqual(by_id[C]['reason'], 'campaign_expiry_within_45_days')
            self.assertEqual(result['platformWrites'], 0)
            self.assertEqual(result['eligible'], 1)

    def test_incomplete_pagination_is_an_error_not_an_empty_list(self):
        class Short(Fake):
            """第一页有 1 条但声称总数 5，之后没有更多页 —— 不完整，必须报错。"""

            def _xhr(self, **kwargs):
                if kwargs.get('write'):
                    return super()._xhr(**kwargs)
                page = int((kwargs.get('params') or {}).get('cur_page') or 1)
                rows = [{'campaign_id': A, 'promotion_end_time': FAR}] if page == 1 else []
                return type('Outcome', (), {'http_status': 200, 'code': 0,
                                            'payload': {'data': {'campaign': rows, 'total': 5}}})()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as caught:
                preview(folder, transport=context(Short()), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(str(caught.exception), 'campaign_list_incomplete')


class Apply(unittest.TestCase):
    def _previewed(self, folder, fake, ids=(A,)):
        preview(folder, transport=context(fake), clock=lambda: NOW, job_id='job-1')
        return fake

    def test_without_confirmation_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A]))
            with self.assertRaises(ValueError) as caught:
                apply(folder, campaign_ids=[A], email='a@b.com', transport=context(fake),
                      clock=lambda: NOW, job_id='job-1')
            self.assertEqual(str(caught.exception), 'campaign_join_confirmation_required')
            self.assertEqual(fake.writes, [])

    def test_a_confirmed_join_writes_once_and_settles_as_joined(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A]))
            result = apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                           transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(result['counts'], {'joined': 1})
            self.assertEqual(len(fake.writes), 1)   # 注入的假传输不维护 platformWrites 计数器
            self.assertEqual(fake.writes[0][1]['contact_info']['campaign_contact_info'][0]['value'], 'a@b.com')

    def test_a_verification_challenge_solves_and_replays_the_same_join_once(self):
        with tempfile.TemporaryDirectory() as folder:
            answers = [outcome(code=10000, turing=True), outcome()]
            fake = self._previewed(folder, Fake(joinable=[A], answer=lambda *_: answers.pop(0)))
            fake._verification_header = 'challenge'
            fake.verification_successes = 0
            fake._solve_verification = lambda header: self.assertEqual(header, 'challenge')
            result = apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                           transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['counts'], {'joined': 1})
            self.assertEqual(len(fake.writes), 2)
            self.assertEqual(fake.writes[0], fake.writes[1])
            self.assertEqual(fake.verification_successes, 1)

    def test_a_confirmed_join_refreshes_the_platform_joined_total(self):
        """写完必须重读"已加入"总数：拿写入之前的数当结论，页面就永远少算这一批。

        回归：18 个加入全部成功、平台 61，账本却还写着 43（那是写入前预览时的读数）。
        """
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A], joined=[])

            def answer(cid, payload):
                # 平台接受之后，子活动出现在"已加入"列表里——这正是最后那次重读能看到的东西。
                fake.joined_rows = [{'campaign_id': '7685682208821184278'}]
                return outcome(returned='7685682208821184278')

            fake.answer = answer
            preview(folder, transport=context(fake), clock=lambda: NOW)
            self.assertEqual(status(folder)['joinedCount'], 0)
            result = apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                           transport=context(fake), clock=lambda: NOW)
            self.assertEqual(result['counts'], {'joined': 1})
            self.assertEqual(status(folder)['joinedCount'], 1)      # 不是写入前的 0

    def test_an_ambiguous_answer_stops_and_is_never_resent(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A, B], answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True)))
            result = apply(folder, campaign_ids=[A, B], email='a@b.com', confirm=True,
                           transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['state'], 'needs_verification')
            self.assertEqual(len(fake.writes), 1)                 # 第二个活动不再尝试
            self.assertEqual(result['counts'].get('result_unknown'), 1)
            # 再点一次 apply：必须拒绝，而不是重新提交
            with self.assertRaises(ValueError) as caught:
                apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                      transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(str(caught.exception), 'campaign_write_requires_verification')
            self.assertEqual(len(fake.writes), 1)

    def test_verify_settles_an_unknown_write_by_reading_back(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A], answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True)))
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW, job_id='job-1')
            # 平台其实已经加入了：回查应当结算成 joined，而且不再产生任何写入
            fake.joined_rows = [{'campaign_id': A}]
            result = verify(folder, transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['settled'], 1)
            self.assertEqual(result['counts'], {'joined': 1})
            self.assertEqual(result['platformWrites'], 0)
            self.assertEqual(len(fake.writes), 1)

    def test_a_child_campaign_id_returned_by_the_platform_still_verifies(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A], answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True, returned=D)))
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW, job_id='job-1')
            fake.joined_rows = [{'campaign_id': D}]          # 平台只把子活动列出来
            result = verify(folder, transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['counts'], {'joined': 1})

    def test_an_explicit_rejection_is_skipped_not_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A], answer=lambda cid, p: outcome(code=40001)))
            result = apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                           transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['counts'], {'skipped': 1})
            self.assertEqual(result['state'], 'completed')

    def test_only_previewed_campaigns_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A]))
            with self.assertRaises(ValueError) as caught:
                apply(folder, campaign_ids=[A, E], email='a@b.com', confirm=True,
                      transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(str(caught.exception), 'campaign_join_not_in_preview')
            self.assertEqual(fake.writes, [])

    def test_a_campaign_that_left_the_joinable_list_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = self._previewed(folder, Fake(joinable=[A]))
            fake.joinable = []                                   # 预览之后平台把它撤了
            result = apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                           transport=context(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['counts'], {'skipped': 1})
            self.assertEqual(fake.writes, [])


class Validation(unittest.TestCase):
    def test_the_payload_freezes_exactly_what_the_legacy_protocol_sent(self):
        self.assertEqual(join_payload('1111111111', 'a@b.com'),
                         {'campaign_id': '1111111111', 'is_joined': True,
                          'contact_info': {'campaign_contact_info': [{'type': 2, 'value': 'a@b.com'}]}})
        for bad in (('abc', 'a@b.com'), ('1111111111', 'not-an-email'), ('1111111111', '')):
            with self.assertRaises(ValueError):
                join_payload(*bad)

    def test_eligibility_matches_the_confirmed_45_day_rule(self):
        now = datetime.fromtimestamp(NOW, timezone.utc)
        self.assertEqual(eligibility({'promotion_end_time': FAR}, at=now), (True, ''))
        self.assertEqual(eligibility({'promotion_end_time': SOON}, at=now),
                         (False, 'campaign_expiry_within_45_days'))
        self.assertEqual(eligibility({}, at=now), (False, 'campaign_end_missing'))

    def test_status_before_anything_ran_is_unavailable_not_a_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(status(folder)['available'])

    def test_the_job_keeps_the_attempt_flag_on_disk_before_the_write(self):
        # 直接检查账本：写入尝试必须先落盘，否则崩溃后会看起来"从没提过"。
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A])
            preview(folder, transport=context(fake), clock=lambda: NOW, job_id='job-1')
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW, job_id='job-1')
            with contextlib.closing(sqlite3.connect(Path(folder) / 'var/campaign-join.sqlite')) as conn:
                row = conn.execute('SELECT state,write_attempted FROM campaign_join_item').fetchone()
            self.assertEqual(row[0], 'joined')
            self.assertEqual(row[1], 1)


if __name__ == '__main__':
    unittest.main()


class Sessions:
    """可重复进入的会话：join_all 会开两次（先只读预览、再写入），fake 只有一个。"""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        return self.fake

    def __exit__(self, *exc):
        return False


class JoinAll(unittest.TestCase):
    """一键加入：先重新预览，再把**当前全部合格**的活动一次提交；不合格的一个都不碰。

    注：注入的假传输不维护 ``platformWrites`` 计数器（既有测试同样如此），所以这里以
    ``fake.writes`` 为准——那才是"到底写了几次"的事实。
    """

    def test_it_previews_then_writes_every_eligible_campaign(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A, B])
            fake.joinable.append({'campaign_id': C, 'name': 'soon', 'promotion_end_time': SOON})
            result = join_all(folder, email='a@b.com', confirm=True, transport=Sessions(fake),
                              clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['eligible'], 2)
            self.assertEqual(sorted(result['attempted']), sorted([A, B]))
            self.assertEqual(len(fake.writes), 2)
            self.assertEqual(result['appliedCounts'], {'joined': 2})
            # 期限不足的那个活动绝不能出现在写入里。
            self.assertEqual([cid for _, payload in fake.writes for cid in [payload['campaign_id']]], [A, B])

    def test_more_than_one_hundred_campaigns_are_joined_in_bounded_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            ids=[f'{index:019d}' for index in range(1,102)]
            fake=Fake(joinable=ids)
            result=join_all(folder,email='a@b.com',confirm=True,transport=Sessions(fake),
                            clock=lambda:NOW,job_id='job-large')
            self.assertEqual(result['eligible'],101)
            self.assertEqual(len(result['attempted']),101)
            self.assertEqual(len(fake.writes),101)
            self.assertEqual(result['appliedCounts'],{'joined':101})

    def test_it_refuses_without_confirmation_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A])
            with self.assertRaises(ValueError):
                join_all(folder, email='a@b.com', transport=Sessions(fake), clock=lambda: NOW, job_id='job-1')
            self.assertEqual(fake.writes, [])

    def test_an_invalid_email_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A])
            with self.assertRaises(ValueError):
                join_all(folder, email='not-an-email', confirm=True, transport=Sessions(fake),
                         clock=lambda: NOW, job_id='job-1')
            self.assertEqual(fake.writes, [])

    def test_nothing_eligible_is_not_a_write(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[], joined=[A])
            result = join_all(folder, email='a@b.com', confirm=True, transport=Sessions(fake),
                              clock=lambda: NOW, job_id='job-1')
            self.assertEqual(result['eligible'], 0)
            self.assertEqual(result['platformWrites'], 0)   # 没写就老老实实报 0
            self.assertEqual(fake.writes, [])

    def test_preview_apply_verify_share_one_job_because_the_cli_does(self):
        """CLI 走 preview 再 apply 时必须看得到预览判定过的条目。

        回归：三个动作默认各用一个 job id，"只能加入预览过的"这道闸门在 CLI 上永远过不去。
        """
        from lib.campaign_join import DEFAULT_JOB_ID
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A])
            preview(folder, transport=context(fake), clock=lambda: NOW)
            result = apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                           transport=context(fake), clock=lambda: NOW)
            self.assertEqual(len(fake.writes), 1)
            self.assertEqual(status(folder)['jobId'], DEFAULT_JOB_ID)


if __name__ == '__main__':
    unittest.main()

class DefaultEmail(unittest.TestCase):
    """页面邮箱的默认值：先看配置，写过一次之后以实际提交过的为准。绝不凭空编一个。"""

    def test_config_email_is_used_until_a_submitted_one_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            (root / 'config/campaign-join.json').write_text(json.dumps({'email': 'default@b.com'}))
            fake = Fake(joinable=[A])
            preview(folder, transport=context(fake), clock=lambda: NOW)
            self.assertEqual(default_email(folder), 'default@b.com')
            self.assertEqual(status(folder)['email'], 'default@b.com')
            apply(folder, campaign_ids=[A], email='used@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW)
            # 提交过的邮箱是证据，优先级高于配置里的默认值。
            self.assertEqual(status(folder)['email'], 'used@b.com')

    def test_a_missing_or_invalid_default_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(default_email(folder), '')
            (root / 'config').mkdir()
            (root / 'config/campaign-join.json').write_text('not json')
            self.assertEqual(default_email(folder), '')
            (root / 'config/campaign-join.json').write_text(json.dumps({'email': 'not-an-email'}))
            self.assertEqual(default_email(folder), '')


if __name__ == '__main__':
    unittest.main()


class CliErrorPath(unittest.TestCase):
    """CLI 崩了也必须以 JSON 回答，否则页面只会显示 campaign_unavailable。

    真实踩过：写入作用域里一个未定义的常量让 CLI 抛 NameError，stdout 空白，
    页面报的是"服务不可用"，真正的原因（名字级错误）一个字都没传到操作者眼前。
    """

    def load_cli(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('campaign_join_cli', ROOT / 'scripts/campaign-join.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_cli(self, argv, patch=None):
        import contextlib
        import io
        cli = self.load_cli()
        if patch:
            patch(cli)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            with unittest.mock.patch.object(sys, 'argv', ['campaign-join.py', *argv]):
                code = cli.main()
        return code, json.loads(out.getvalue())

    def test_an_unexpected_exception_still_answers_json_with_the_reason(self):
        from unittest import mock

        def boom(_root):
            raise RuntimeError('作用域里的未定义名字')

        with mock.patch.dict(sys.modules):
            code, payload = self.run_cli(['preview'], lambda cli: setattr(cli, 'preview', boom))
        self.assertEqual(code, 2)
        self.assertIn('campaign_join_internal:RuntimeError', payload['error'])
        self.assertIn('未定义名字', payload['error'])

    def test_a_known_refusal_keeps_its_own_code(self):
        code, payload = self.run_cli(['join-all', '--email', 'a@b.com'])
        self.assertEqual((code, payload['error']), (2, 'campaign_join_confirmation_required'))


class WriteProtection(unittest.TestCase):
    """写入保护：结果未知绝不重试、已加入总数以平台为准。"""

    def test_preview_never_erases_an_attempted_write(self):
        """预览是"先预览再提交"（一键加入）的第一步，它不能把禁重试保护清掉。

        回归：`preview` 原来整表替换条目，write_attempted 一并归零——于是下一次提交
        会重复写平台，而"结果未知绝不重试"正是这条状态机存在的理由。
        """
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A],
                        answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True))
            preview(folder, transport=context(fake), clock=lambda: NOW)
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW)
            self.assertEqual(len(fake.writes), 1)
            preview(folder, transport=context(fake), clock=lambda: NOW)      # 再预览一次
            row = next(r for r in status(folder)['items'] if r['campaign_id'] == A)
            self.assertEqual((row['state'], row['write_attempted']), ('result_unknown', 1))
            with self.assertRaises(ValueError) as caught:
                apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                      transport=context(fake), clock=lambda: NOW)
            self.assertEqual(str(caught.exception), 'campaign_write_requires_verification')
            self.assertEqual(len(fake.writes), 1)                            # 绝没有第二次写

    def test_preview_settles_an_attempted_item_the_platform_now_calls_joined(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A],
                        answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True))
            preview(folder, transport=context(fake), clock=lambda: NOW)
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW)
            fake.joined_rows = [{'campaign_id': A}]      # 平台后来说它已经加入了
            preview(folder, transport=context(fake), clock=lambda: NOW)
            row = next(r for r in status(folder)['items'] if r['campaign_id'] == A)
            self.assertEqual(row['state'], 'joined')

    def test_a_settled_skip_becomes_joinable_again_after_a_fresh_preview(self):
        """已结算的 skipped 必须按平台这次的回答刷新，否则它会永远卡住、再点也进不去。"""
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A],
                        answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True))
            preview(folder, transport=context(fake), clock=lambda: NOW)
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW)
            verify(folder, transport=context(fake), clock=lambda: NOW)      # 结算成 skipped
            row = next(r for r in status(folder)['items'] if r['campaign_id'] == A)
            self.assertEqual(row['state'], 'skipped')
            preview(folder, transport=context(fake), clock=lambda: NOW)     # 平台仍把它列为可加入
            row = next(r for r in status(folder)['items'] if r['campaign_id'] == A)
            self.assertEqual(row['state'], 'eligible')

    def test_verify_records_the_platform_total_not_a_job_local_count(self):
        """回归：回查曾把"已加入总数"写成"本 job 里 joined 的条目数"，一次回查就把 43 变成 0。"""
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A], joined=[B, C],
                        answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True))
            preview(folder, transport=context(fake), clock=lambda: NOW)
            self.assertEqual(status(folder)['joinedCount'], 2)
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW)
            verify(folder, transport=context(fake), clock=lambda: NOW)
            self.assertEqual(status(folder)['joinedCount'], 2)               # 不能被写成 0

    def test_verify_settles_a_write_the_platform_says_is_not_joined(self):
        """平台把它列在"可加入"里、又不在"已加入"里：这是平台自己的两个回答，据此结算。"""
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A],
                        answer=lambda cid, p: outcome(http=200, code=None, ambiguous=True))
            preview(folder, transport=context(fake), clock=lambda: NOW)
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW)
            result = verify(folder, transport=context(fake), clock=lambda: NOW)
            self.assertEqual(result['settled'], 1)
            row = next(r for r in status(folder)['items'] if r['campaign_id'] == A)
            self.assertEqual(row['state'], 'skipped')
            self.assertIn('joinable', row['reason'])

    def test_verify_refreshes_the_count_even_with_nothing_to_settle(self):
        """没有待结算项时也要读平台：这个动作同时负责把"已加入总数"对齐到平台。"""
        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A], joined=[])
            preview(folder, transport=context(fake), clock=lambda: NOW)
            self.assertEqual(status(folder)['joinedCount'], 0)
            fake.joined_rows = [{'campaign_id': B}, {'campaign_id': C}]
            result = verify(folder, transport=context(fake), clock=lambda: NOW)
            self.assertEqual(result['settled'], 0)        # 没有待结算项
            self.assertEqual(result['joinedCount'], 2)    # 但总数要对齐平台
            self.assertTrue(result['available'])          # 而且必须是统一的封装

    def test_an_unknown_write_keeps_the_error_message(self):
        """结果未知时的原因必须带消息：只留类型名查不出根因（实际吃过这个亏）。"""
        class TapLinkError(Exception):
            """旧版传输在写入未被允许时抛的类型；这里只需要它的名字和消息。"""

        with tempfile.TemporaryDirectory() as folder:
            fake = Fake(joinable=[A],
                        answer=lambda cid, p: (_ for _ in ()).throw(TapLinkError('taplink_endpoint_not_allowed')))
            preview(folder, transport=context(fake), clock=lambda: NOW)
            apply(folder, campaign_ids=[A], email='a@b.com', confirm=True,
                  transport=context(fake), clock=lambda: NOW)
            row = next(r for r in status(folder)['items'] if r['campaign_id'] == A)
            self.assertIn('taplink_endpoint_not_allowed', row['reason'])


if __name__ == '__main__':
    unittest.main()


class OtherCategories(unittest.TestCase):
    """其它分类（平台活动）：加入的查询只覆盖 Seller collabs 一类。

    实测平台的分类：`crs_campaign_type` 4=Seller collabs 可加入、5=其已加入的列法、
    6=平台活动（父）、7=平台活动（子）。**已加入列表存的是子活动 id**，
    所以按 id 直接做差集会得出"漏了 3 个"的假结论——这里必须按父子关系对照。
    """

    def transport(self, parents, subs):
        class T:
            def _params(self):
                return {}

            def _xhr(self, *, method, path, params=None, payload=None, write=False):
                kind = str((params or {}).get('crs_campaign_type'))
                rows = parents if kind == '6' else subs
                return type('O', (), {'http_status': 200, 'code': 0,
                                      'payload': {'data': {'campaign': rows, 'total': len(rows)}}})()

            def require_read(self, outcome):
                return outcome.payload
        return T()

    def test_a_joined_parent_is_not_reported_as_a_missing_campaign(self):
        parent = {'campaign_id': '7674910822372099862', 'name': 'IT Q3 POP Core Product',
                  'sub_campaign_id': '7683821197913540374', 'status': 3,
                  'promotion_end_time': FAR}
        sub = {'campaign_id': '7683821197913540374', 'name': 'IT Q3 POP Core Product',
               'status': 3, 'promotion_end_time': FAR}
        # 子活动已在"已加入"里：父活动 id 与子活动 id 不同，不能据此说"漏了"。
        built = read_other_categories(self.transport([parent], [sub]), {'7683821197913540374'},
                                      at=datetime.fromtimestamp(NOW, timezone.utc))
        self.assertEqual((built['parents'], built['subs'], built['unjoined']), (1, 1, []))

    def test_an_unjoined_ended_sub_is_reported_with_its_reason(self):
        sub = {'campaign_id': '7666374823451658007', 'name': 'IT Ops Campaign Core Product （March）',
               'status': 4, 'promotion_end_time': SOON}
        built = read_other_categories(self.transport([], [sub]), set(),
                                      at=datetime.fromtimestamp(NOW, timezone.utc))
        self.assertEqual(built['unjoinedEligible'], 0)
        self.assertEqual(built['unjoined'][0]['campaignId'], '7666374823451658007')
        self.assertIn(built['unjoined'][0]['reason'], ('campaign_ended', 'campaign_expiry_within_45_days'))

    def test_an_unreadable_category_is_not_reported_as_zero(self):
        class Broken:
            def _params(self):
                return {}

            def _xhr(self, **kwargs):
                raise ValueError('campaign_list_incomplete')

            def require_read(self, outcome):
                return outcome.payload
        built = read_other_categories(Broken(), set())
        self.assertFalse(built['available'])       # 读不到就说读不到，不报 0


if __name__ == '__main__':
    unittest.main()

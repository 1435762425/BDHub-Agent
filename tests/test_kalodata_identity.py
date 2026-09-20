"""Kalodata identity adapter: what it refuses, what it stores, and what it must never expose."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.kalodata_identity import (DEFAULTS, VERDICTS, config_path, cookie_state, load,  # noqa: E402
                                   login_state, probe_pid, read_state, resolve_grabber, response_row_count,
                                   save, status, validate)


class CardAndConfig(unittest.TestCase):
    def test_defaults_are_empty_and_do_not_invent_a_card(self):
        self.assertEqual(validate({})['activationCode'], '')
        self.assertEqual(validate({})['canaryPid'], '')

    def test_a_card_that_cannot_be_typed_into_the_extension_form_is_refused(self):
        for bad in [{'activationCode': 'abc\ndef'}, {'activationCode': 'a\tb'},
                    {'activationCode': 'x' * 201}, {'canaryPid': 'not-a-pid'},
                    {'canaryPid': '123'}, 'nope']:
            with self.assertRaises(ValueError):
                validate(bad)

    def test_the_card_file_is_owner_only_and_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = save(folder, {'activationCode': 'CARD-1234', 'canaryPid': '1729480002729777701'})
            self.assertEqual(load(folder), saved)
            self.assertEqual(load(folder)['activationCode'], 'CARD-1234')
            mode = stat.S_IMODE(os.stat(config_path(folder)).st_mode)
            self.assertEqual(mode, 0o600)

    def test_a_rejected_card_never_touches_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            save(folder, {'activationCode': 'KEEP'})
            with self.assertRaises(ValueError):
                save(folder, {'activationCode': 'bad\nvalue'})
            self.assertEqual(load(folder)['activationCode'], 'KEEP')

    def test_an_absent_config_reads_as_the_safe_default(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load(folder), validate(DEFAULTS))

    def test_missing_legacy_grabber_path_uses_an_existing_local_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);configured=root/'old-name';fallback=root/'current-name';fallback.mkdir()
            self.assertEqual(resolve_grabber(configured,fallback),fallback)
            configured.mkdir();self.assertEqual(resolve_grabber(configured,fallback),configured)


class CookieIsNeverRead(unittest.TestCase):
    def test_cookie_state_reports_existence_only(self):
        state = cookie_state()
        self.assertEqual(set(state), {'present', 'updatedAt', 'bytes'})
        self.assertIsInstance(state['present'], bool)

    def test_the_cookie_file_is_not_in_this_repository(self):
        from lib.kalodata_identity import cookie_path
        cookie = cookie_path().resolve()
        self.assertFalse(cookie.is_relative_to(ROOT.resolve()))


class Verdicts(unittest.TestCase):
    def test_an_exhausted_quota_counts_as_a_healthy_identity(self):
        # The platform accepting the request and answering "quota used up" proves the session works.
        self.assertTrue(VERDICTS['quota_exhausted'])
        self.assertTrue(VERDICTS['ready'])
        self.assertFalse(VERDICTS['auth_required'])
        # A timeout or a business rejection is not evidence either way.
        self.assertIsNone(VERDICTS['unreachable'])
        self.assertIsNone(VERDICTS['business_rejected'])

    def test_status_reads_files_only_and_reports_no_scheduler_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            state = status(folder)
            self.assertEqual(state['probeEndpoint'], 'kalodata')
            self.assertIsNone(state['lastProbe'])
            self.assertIsNone(state['login'])
            self.assertEqual(state['probePid'], '')  # no collection database under a temp root
            self.assertIn('probeHint', state)

    def test_the_last_probe_is_reported_back_after_a_run(self):
        with tempfile.TemporaryDirectory() as folder:
            record = {'verdict': 'quota_exhausted', 'identityOk': True, 'detail': 'kalodata_daily_quota_exhausted',
                      'pid': '1729480002729777701', 'rows': None, 'proxyConfigured': False,
                      'checkedAt': 1789400000.0, 'elapsedSeconds': 0.68}
            path = Path(folder) / 'var'
            path.mkdir(parents=True)
            (path / 'kalodata-identity.json').write_text(json.dumps(record), encoding='utf-8')
            self.assertEqual(read_state(folder)['verdict'], 'quota_exhausted')
            self.assertEqual(status(folder)['lastProbe']['verdict'], 'quota_exhausted')

    def test_a_login_that_already_ended_is_not_reported_as_running(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'var'
            path.mkdir(parents=True)
            (path / 'kalodata-login.json').write_text(
                json.dumps({'mode': 'activate', 'pid': 999999999, 'startedAt': 1.0, 'log': 'x'}), encoding='utf-8')
            state = login_state(folder)
            self.assertFalse(state['running'])

    def test_the_probe_target_falls_back_to_a_collected_product(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(probe_pid(folder, validate({'canaryPid': '1729480002729777701'})),
                             '1729480002729777701')
            self.assertEqual(probe_pid(folder, validate({})), '')

    def test_probe_counts_both_list_and_wrapped_response_shapes(self):
        self.assertEqual(response_row_count({'data':[{},{}]}),2)
        self.assertEqual(response_row_count({'data':{'list':[{}]}}),1)
        self.assertEqual(response_row_count({'data':{'items':[{}, {}, {}]}}),3)
        self.assertEqual(response_row_count({'data':None}),0)


if __name__ == '__main__':
    unittest.main()

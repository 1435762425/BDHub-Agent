from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
from contextlib import nullcontext

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.kalodata_login import initialize_session,export_proxy_fallback,verified_probe
from lib.kalodata_login import run_login
import tempfile,json


class ExtensionIdentityTests(unittest.TestCase):
    def launcher(self):
        launcher=Mock();launcher.wait_for_extension_id.return_value='extension'
        launcher.refresh_extension_login.return_value=True
        launcher.export_extension_runtime.return_value=True
        return launcher

    def test_activation_exports_new_cookie_with_matching_route_without_old_cookie_injection(self):
        launcher=self.launcher();context=Mock()
        self.assertEqual(initialize_session(launcher,context,'activate','saved-code'),'extension')
        launcher.activate_and_login_extension.assert_called_once_with(context,'extension','saved-code')
        launcher.read_saved_cookie.assert_not_called();context.add_cookies.assert_not_called()
        names=[c[0] for c in launcher.method_calls]
        self.assertLess(names.index('activate_and_login_extension'),names.index('export_extension_runtime'))
        self.assertLess(names.index('export_extension_runtime'),names.index('export_current_cookie'))

    def test_optional_proxy_does_not_override_the_real_provider_verdict(self):
        launcher=self.launcher();launcher.export_extension_runtime.return_value=False
        self.assertEqual(initialize_session(launcher,Mock(),'refresh',wait_seconds=0),'extension')
        launcher.export_current_cookie.assert_called_once()
        for result in ({'success':True,'hasUpdate':True},{'verdict':'auth_required','identityOk':False},
                       {'verdict':'ready','identityOk':None}):self.assertFalse(verified_probe(result))
        for verdict in ('ready','quota_exhausted'):
            self.assertTrue(verified_probe({'verdict':verdict,'identityOk':True}))

    def test_refresh_uses_saved_card_and_only_activates_when_missing(self):
        launcher=self.launcher();context=Mock()
        initialize_session(launcher,context,'refresh','fallback-code')
        launcher.activate_and_login_extension.assert_not_called()

    def test_empty_runtime_proxy_falls_back_to_saved_proxy_and_keeps_credentials_private(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher=self.launcher();launcher.EXTENSION_RUNTIME_PATH=Path(directory)/'runtime.json'
            worker=Mock(url='chrome-extension://extension/background.js')
            worker.evaluate.return_value={'runtime':{},'saved':{'proxy':'127.0.0.1|8000|demo user|demo:password'}}
            context=Mock(service_workers=[worker])
            self.assertTrue(export_proxy_fallback(launcher,context,'extension'))
            record=json.loads(launcher.EXTENSION_RUNTIME_PATH.read_text())
            self.assertEqual(record['proxy_url'],'http://demo%20user:demo%3Apassword@127.0.0.1:8000')
            self.assertEqual(launcher.EXTENSION_RUNTIME_PATH.stat().st_mode&0o777,0o600)

    def test_failed_extension_does_not_export_browser_preferences_as_a_login_cookie(self):
        launcher=self.launcher();context=Mock(pages=[])
        launcher.sync_playwright.return_value=nullcontext(Mock())
        launcher.launch_context.return_value=context
        with tempfile.TemporaryDirectory() as directory, \
             patch('lib.kalodata_identity.export_helpers',return_value=launcher), \
             patch('lib.kalodata_login.initialize_session',side_effect=ValueError('kalodata_extension_missing')), \
             patch('lib.kalodata_login.signal.signal'),patch('builtins.print'):
            self.assertEqual(run_login(Path(directory),'activate'),1)
        launcher.export_current_cookie.assert_not_called()
        launcher.refresh_extension_login.return_value=False
        initialize_session(launcher,context,'refresh','fallback-code')
        launcher.activate_and_login_extension.assert_called_once_with(context,'extension','fallback-code')

    def test_missing_extension_or_card_cannot_start_an_unrelated_login(self):
        launcher=self.launcher();launcher.wait_for_extension_id.return_value=''
        with self.assertRaisesRegex(ValueError,'kalodata_extension_missing'):initialize_session(launcher,Mock(),'refresh')
        launcher=self.launcher();launcher.refresh_extension_login.return_value=False
        with self.assertRaisesRegex(ValueError,'kalodata_saved_activation_missing'):initialize_session(launcher,Mock(),'refresh')
        launcher.activate_and_login_extension.assert_not_called()


if __name__=='__main__':unittest.main()

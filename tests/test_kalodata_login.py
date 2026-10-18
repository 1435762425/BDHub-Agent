from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.kalodata_login import initialize_session


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

    def test_failed_proxy_export_never_reports_identity_export_complete(self):
        launcher=self.launcher();launcher.export_extension_runtime.return_value=False
        with self.assertRaisesRegex(ValueError,'kalodata_proxy_export_failed'):
            initialize_session(launcher,Mock(),'refresh')
        launcher.export_current_cookie.assert_not_called()

    def test_refresh_uses_saved_card_and_only_activates_when_missing(self):
        launcher=self.launcher();context=Mock()
        initialize_session(launcher,context,'refresh','fallback-code')
        launcher.activate_and_login_extension.assert_not_called()
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

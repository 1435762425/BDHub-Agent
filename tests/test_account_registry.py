import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.account_registry import AccountRegistryError, add, listing, registry_path, update_credentials  # noqa: E402


class AccountRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "config").mkdir()
        (self.root / "config/market-accounts.json").write_text(json.dumps(
            {"markets": {"it": {"roles": {"communications": "acc6", "supply": "acc9"}}}}))

    def tearDown(self):
        self.directory.cleanup()

    def register(self, account="acc6", **changes):
        values = {"account": account, "market": "it", "partner_id": "7495", "username": "user@example.com",
                  "password": "PRIVATE_PASSWORD"} | changes
        return add(self.root, **values)

    def test_listing_reports_assignment_but_never_secrets(self):
        result = self.register()
        self.register("acc12", market="uk", partner_id="8800")
        self.assertNotIn("PRIVATE_PASSWORD", json.dumps(result))
        rows = {row["account"]: row for row in listing(self.root)["accounts"]}
        self.assertEqual(rows["acc6"]["assignment"], {"market": "it", "role": "communications"})
        self.assertIsNone(rows["acc12"]["assignment"])
        self.assertTrue(rows["acc6"]["passwordSet"] and rows["acc6"]["usernameSet"])
        mode = stat.S_IMODE(registry_path(self.root).stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_duplicate_or_invalid_records_are_refused_without_touching_the_file(self):
        self.register()
        before = registry_path(self.root).read_bytes()
        cases = [("account_exists", {}), ("account_name_invalid", {"account": "ACC7"}),
                 ("account_market_invalid", {"account": "acc7", "market": "ITA"}),
                 ("account_partner_id_invalid", {"account": "acc7", "partner_id": "x1"}),
                 ("account_password_invalid", {"account": "acc7", "password": " padded"})]
        for code, changes in cases:
            with self.subTest(code=code), self.assertRaisesRegex(AccountRegistryError, code):
                self.register(**changes)
        self.assertEqual(registry_path(self.root).read_bytes(), before)

    def test_update_changes_only_the_given_credential(self):
        self.register()
        update_credentials(self.root, account="acc6", password="NEW_PRIVATE")
        row = json.loads(registry_path(self.root).read_text())["accounts"][0]
        self.assertEqual((row["username"], row["password"], row["partner_id"]), ("user@example.com", "NEW_PRIVATE", "7495"))
        with self.assertRaisesRegex(AccountRegistryError, "account_missing"):
            update_credentials(self.root, account="acc7", password="x")

    def test_vendored_loader_reads_the_registry_format(self):
        self.register()
        from lib.legacy_runtime import configure_vendored_bdhub
        config = configure_vendored_bdhub(root=ROOT)
        account = config._bdhub_agent_base_load_accounts(SimpleNamespace(partner_id=""), path=registry_path(self.root))[0]
        self.assertEqual((account.name, account.market, account.partner_id, account.password),
                         ("acc6", "it", "7495", "PRIVATE_PASSWORD"))


if __name__ == "__main__":
    unittest.main()

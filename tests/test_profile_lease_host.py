from dataclasses import asdict
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.legacy_runtime import configure_vendored_bdhub
configure_vendored_bdhub(root=ROOT)
from bdhub.enrich import profile_lease as leases


class ProfileLeaseHostTest(unittest.TestCase):
    def tearDown(self):
        leases._local_host_identity.cache_clear()
        leases._boot_started_at.cache_clear()

    def test_dhcp_hostname_change_keeps_same_machine_identity(self):
        hardware = plistlib.dumps([{'IOPlatformUUID': '9fb9c1aa-84f6-4309-80f4-4502d6bd7b30'}])
        with patch.object(leases.sys, 'platform', 'darwin'), \
             patch.object(leases.subprocess, 'run', return_value=Mock(stdout=hardware)), \
             patch.object(leases.socket, 'gethostname', return_value='192.168.1.6') as hostname:
            first = leases._local_host_identity()
            leases._local_host_identity.cache_clear()
            hostname.return_value = '192.168.1.16'
            self.assertEqual(leases._local_host_identity(), first)
            self.assertTrue(first.startswith('macos-'))

    def test_invalid_hardware_id_does_not_identify_all_hosts_as_one(self):
        with patch.object(leases.sys, 'platform', 'darwin'), \
             patch.object(leases.subprocess, 'run', return_value=Mock(stdout=plistlib.dumps([{}]))), \
             patch.object(leases.socket, 'gethostname', return_value='legacy-host'):
            self.assertEqual(leases._local_host_identity(), 'legacy-host')

    def test_reboot_recovers_local_owner_even_when_pid_reused(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(leases, '_local_host_identity', return_value='macos-this-machine'), \
             patch.object(leases, '_boot_started_at', return_value=2000000000):
            lease = leases.ProfileLease(folder, account='acc6', market='it', operation='test', pid_alive=lambda _: True)
            payload = asdict(lease._new_owner())
            payload['started_at'] = '2020-01-01T00:00:00+00:00'
            lease.path.write_text(json.dumps(payload))
            with lease:
                self.assertNotEqual(lease.owner.token, payload['token'])
            self.assertFalse(lease.path.exists())

    def test_active_foreign_and_unproven_legacy_owners_remain_locked(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(leases, '_local_host_identity', return_value='macos-this-machine'), \
             patch.object(leases.socket, 'gethostname', return_value='current-host'), \
             patch.object(leases, '_boot_started_at', return_value=1000000000):
            lease = leases.ProfileLease(folder, account='acc6', market='it', operation='test', pid_alive=lambda _: True)
            for host in ('macos-this-machine', 'macos-other-machine', 'old-dhcp-host'):
                payload = asdict(lease._new_owner()); payload['hostname'] = host
                lease.path.write_text(json.dumps(payload))
                with self.subTest(host=host), self.assertRaises(leases.ProfileBusyError):
                    lease.acquire()
                self.assertEqual(json.loads(lease.path.read_text()), payload)
            lease._pid_alive = lambda _: False
            payload['started_at'] = '2000-01-01T00:00:00+00:00'
            lease.path.write_text(json.dumps(payload))
            with self.assertRaises(leases.ProfileBusyError):
                lease.acquire()


if __name__ == '__main__':
    unittest.main()

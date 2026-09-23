"""Offline update/check contracts; every write stays inside a temporary fixture."""
from contextlib import redirect_stderr, redirect_stdout
import difflib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vendor_legacy', ROOT / 'scripts/vendor-legacy-bdhub.py')
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class VendorSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy = self.root / 'legacy'
        self.out = self.root / 'vendor'
        self.manifest_path = self.root / 'manifest.json'
        self.sources = {
            'bdhub/__init__.py': b'',
            'bdhub/market.py': b'VALUE = 1\n',
            M.RESOURCE_PATH: json.dumps({'files': {'runtime.py': 'a' * 64}}).encode(),
        }
        self.desired = self.sources | {'bdhub/market.py': b'VALUE = 2\n'}
        self.configure()

    def configure(self, previous=None):
        patches = []
        rows = []
        for name, content in self.sources.items():
            source = self.legacy / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(content)
            desired = self.desired[name]
            rows.append({'path': name, 'source': 'upstream',
                         'upstreamSha256': M.digest(content), 'sha256': M.digest(desired)})
            if content != desired:
                patches.extend(difflib.unified_diff(content.decode().splitlines(True),
                    desired.decode().splitlines(True), fromfile='a/' + name, tofile='b/' + name))
        data = ''.join(patches).encode()
        (self.root / 'local.patch').write_bytes(data)
        self.manifest = {'schemaVersion': 1, 'files': rows,
                         'patch': {'file': 'local.patch', 'sha256': M.digest(data)}}
        if previous:
            self.manifest['previousSnapshot'] = previous
        self.save_manifest()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def run_tool(self, action='--check', out=None):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = M.main([action, '--legacy', str(self.legacy), '--out', str(out or self.out),
                             '--manifest', str(self.manifest_path)])
        return result, output.getvalue()

    def write_snapshot(self):
        result, output = self.run_tool('--write')
        self.assertEqual(result, 0, output)

    def snapshot(self):
        return {str(p.relative_to(self.out)): p.read_bytes() for p in self.out.rglob('*') if p.is_file()}

    def test_missing_target_is_nonzero_and_does_not_create_output(self):
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn('missing:', output)
        self.assertFalse(self.out.exists())

    def test_rebuild_and_atomic_rebuild_preserve_patch_resource_and_upstream(self):
        for _ in range(2):
            self.write_snapshot()
            self.assertEqual(self.snapshot(), self.desired)
            self.assertEqual(self.run_tool()[0], 0)
            self.assertEqual(list(self.out.glob('.vendor-stage-*')), [])
        self.assertEqual((self.legacy / 'bdhub/market.py').read_bytes(), self.sources['bdhub/market.py'])

    def test_missing_resource_is_detected_and_not_silently_repaired_over_existing_tree(self):
        self.write_snapshot()
        (self.out / M.RESOURCE_PATH).unlink()
        self.assertIn(M.RESOURCE_PATH, self.run_tool()[1])
        before = self.snapshot()
        self.assertEqual(self.run_tool('--write')[0], 1)
        self.assertEqual(self.snapshot(), before)

    def test_extra_source_and_unreviewed_local_change_are_rejected(self):
        self.write_snapshot()
        for path, content in [('bdhub/extra.py', b'EXTRA = 1\n'), ('bdhub/market.py', b'VALUE = 99\n')]:
            with self.subTest(path=path):
                target = self.out / path
                original = target.read_bytes() if target.exists() else None
                target.write_bytes(content)
                self.assertEqual(self.run_tool()[0], 1)
                self.assertEqual(self.run_tool('--write')[0], 1)
                self.assertEqual(target.read_bytes(), content)
                target.unlink() if original is None else target.write_bytes(original)

    def test_upstream_change_requires_review_and_preserves_target(self):
        self.write_snapshot()
        before = self.snapshot()
        (self.legacy / 'bdhub/market.py').write_bytes(b'VALUE = 99\n')
        for action in ('--check', '--write'):
            code, output = self.run_tool(action)
            self.assertEqual(code, 1)
            self.assertIn('upstream_changed_review_required', output)
        self.assertEqual(self.snapshot(), before)

    def test_reviewed_previous_revision_can_upgrade_atomically(self):
        self.write_snapshot()
        previous = {name: M.digest(data) for name, data in self.desired.items()}
        self.sources['bdhub/market.py'] = b'VALUE = 3\n'
        self.desired['bdhub/market.py'] = b'VALUE = 4\n'
        self.configure(previous)
        self.assertEqual(self.run_tool()[0], 1)
        self.write_snapshot()
        self.assertEqual(self.snapshot(), self.desired)
        self.assertEqual(self.run_tool()[0], 0)

    def test_patch_tamper_is_rejected(self):
        (self.root / 'local.patch').write_bytes(b'not the reviewed patch')
        code, output = self.run_tool('--write')
        self.assertEqual(code, 1)
        self.assertIn('manifest_patch_changed', output)
        self.assertFalse(self.out.exists())

    def test_bad_staged_syntax_never_replaces_existing_tree(self):
        self.write_snapshot()
        before = self.snapshot()
        previous = {name: M.digest(data) for name, data in before.items()}
        self.desired['bdhub/market.py'] = b'def bad(:\n'
        self.configure(previous)
        code, output = self.run_tool('--write')
        self.assertEqual(code, 1)
        self.assertIn('staged_snapshot_invalid', output)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.out.glob('.vendor-stage-*')), [])

    def test_atomic_exchange_failure_leaves_existing_tree_intact(self):
        self.write_snapshot()
        before = self.snapshot()
        with patch.object(M, 'atomic_publish', side_effect=M.VendorError('exchange_failed')):
            self.assertEqual(self.run_tool('--write')[0], 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.out.glob('.vendor-stage-*')), [])

    def test_missing_resource_in_manifest_is_rejected(self):
        self.manifest['files'] = [r for r in self.manifest['files'] if r['path'] != M.RESOURCE_PATH]
        self.save_manifest()
        self.assertIn('manifest_runtime_resource_missing', self.run_tool('--write')[1])
        self.assertFalse(self.out.exists())

    def test_unlisted_sensitive_resource_and_path_escape_are_rejected(self):
        for name in ('bdhub/secrets.json', 'bdhub/../outside.py', '/tmp/outside.py'):
            with self.subTest(name=name):
                self.manifest['files'][0]['path'] = name
                self.save_manifest()
                self.assertEqual(self.run_tool('--write')[0], 1)
                self.assertFalse(self.out.exists())

    def test_source_target_overlap_cannot_write_the_upstream_package(self):
        before = (self.legacy / 'bdhub/market.py').read_bytes()
        code, output = self.run_tool('--write', out=self.legacy)
        self.assertEqual(code, 1)
        self.assertIn('source_target_overlap', output)
        self.assertEqual((self.legacy / 'bdhub/market.py').read_bytes(), before)

    def test_target_symlink_is_rejected_without_touching_destination(self):
        self.out.mkdir()
        (self.out / 'bdhub').symlink_to(self.legacy / 'bdhub', target_is_directory=True)
        self.assertEqual(self.run_tool('--write')[0], 1)
        self.assertTrue((self.out / 'bdhub').is_symlink())

    def test_runtime_caches_do_not_become_source_inputs(self):
        self.write_snapshot()
        cache = self.out / 'bdhub/__pycache__'
        cache.mkdir()
        (cache / 'market.cpython-313.pyc').write_bytes(b'cache')
        self.assertEqual(self.run_tool()[0], 0)
        self.write_snapshot()
        self.assertEqual(self.snapshot(), self.desired)

    def test_staging_inside_checkout_ignores_inherited_git_worktree(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        with patch.dict(os.environ, {'GIT_WORK_TREE': str(self.root), 'GIT_DIR': str(self.root / '.git')}):
            self.write_snapshot()
        self.assertEqual(self.snapshot(), self.desired)
        self.assertFalse((self.root / 'bdhub').exists())


if __name__ == '__main__':
    unittest.main()

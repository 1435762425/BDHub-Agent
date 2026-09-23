from pathlib import Path
import importlib.util
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_docs', ROOT / 'scripts/check-docs.py')
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class DocumentationCheckTests(unittest.TestCase):
    def test_all_repository_markdown_including_untracked_is_checked_but_runtime_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', directory], check=True)
            (root / '.gitignore').write_text('var/\nnode_modules/\n')
            names = ['README.md', 'vendor/README.md', 'design/brief.md',
                     'apps/agent/skills/example.md', 'apps/web/THIRD_PARTY_NOTICES.md']
            for name in [*names, 'var/evidence.md', 'node_modules/package/README.md']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# Test\n')
            self.assertEqual({str(p.relative_to(root)) for p in M.markdown_files(root)}, set(names))

    def test_existing_and_duplicate_heading_anchors_and_missing_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            doc = root / 'README.md'
            doc.write_text('# 说明：当前状态\n# 说明：当前状态\n'
                           '[first](#说明当前状态)\n[second](#说明当前状态-1)\n'
                           '[bad](#已不存在)\n')
            errors = M.validate_links([doc], root)
            self.assertEqual(len(errors), 1)
            self.assertIn('missing heading anchor', errors[0])

    def test_images_encoded_paths_angle_paths_and_remote_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a b.png').write_bytes(b'fixture')
            doc = root / 'README.md'
            doc.write_text('![asset](a%20b.png)\n[asset](<a b.png>)\n'
                           '[remote](https://example.test/path#heading)\n'
                           '[title](a%20b.png "Title")\n![bad](missing.png)\n')
            errors = M.validate_links([doc], root)
            self.assertEqual(len(errors), 1)
            self.assertIn('missing.png', errors[0])

    def test_external_historical_reference_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            doc = root / 'README.md'
            doc.write_text('[legacy](../sibling/README.md)\n')
            self.assertEqual(M.validate_links([doc], root), [])


if __name__ == '__main__':
    unittest.main()

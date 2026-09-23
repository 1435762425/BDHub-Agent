import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'scripts/resume-global-category.py'
SPEC = importlib.util.spec_from_file_location('resume_global_category_cli', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ResumeGlobalCategoryTests(unittest.TestCase):
    def test_total_drift_without_duplicate_pages_does_not_loop_repair(self):
        self.assertEqual(MODULE.repair_decision({'attempt': 1, 'pages': []}),
                         'total_drift_without_duplicate_pages')
        self.assertEqual(MODULE.repair_decision({'attempt': 4, 'pages': [145, 146]}), 'repair')
        self.assertEqual(MODULE.repair_decision({'attempt': 5, 'pages': [145, 146]}), 'repair_exhausted')


if __name__ == '__main__': unittest.main()

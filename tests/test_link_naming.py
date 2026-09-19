"""Naming config: validation, rendering, and the platform length limit."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.link_naming import DEFAULTS, fingerprint, load, name_for, render, render_full, save, short_name_for, tail_for, validate  # noqa: E402


class NamingConfig(unittest.TestCase):
    def test_default_template_matches_the_existing_cards(self):
        name = render(DEFAULTS, short_name='quaderni di calligrafia', creator_percent='13', tail='0ce672')
        self.assertEqual(name, '🔥 BJN quaderni di calligrafia 13% 0ce672')

    def test_unknown_placeholder_and_length_limits_are_rejected(self):
        for bad in [{'template': 'BJN {pid} {tail}'},
                    {'template': 'BJN {short_name} {tail'},
                    {'template': '   '},
                    {'template': 'BJN {short_name}', 'tailLength': 2},
                    {'template': 'BJN {short_name}', 'maxLength': 80},
                    {'template': 'BJN {short_name}', 'shortNameMaxLength': 0},
                    {'template': 'BJN {short_name}', 'tailLength': 'six'}]:
            with self.assertRaises(ValueError):
                validate(bad)

    def test_short_name_is_trimmed_until_the_platform_limit_is_met(self):
        long_title = 'pantaloni cargo da donna in denim con stampa floreale'
        rendered = render_full(DEFAULTS, short_name=long_title, creator_percent='13', tail='abcdef')
        self.assertLessEqual(len(rendered['name']), 50)
        self.assertTrue(rendered['name'].startswith('🔥 BJN pantaloni'))
        self.assertTrue(rendered['name'].endswith('13% abcdef'))

    def test_a_template_that_cannot_fit_is_refused(self):
        config = validate({'template': 'BJN {short_name} ' + 'x' * 40 + ' {tail}', 'maxLength': 50})
        with self.assertRaises(ValueError):
            render(config, short_name='abc', creator_percent='13', tail='abcdef')

    def test_tail_is_deterministic_and_template_length_follows_the_config(self):
        self.assertEqual(tail_for('123', '456', '13', DEFAULTS), tail_for('123', '456', '13', DEFAULTS))
        short = validate({'template': 'BJN {short_name} {tail}', 'tailLength': 4})
        self.assertEqual(len(tail_for('123', '456', '13', short)), 4)

    def test_save_rejects_before_touching_the_file_and_then_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config/link-naming.json'
            with self.assertRaises(ValueError):
                save(tmp, {'template': 'BJN {short_name} {tail}'[:5]})
            self.assertFalse(path.exists())
            saved = save(tmp, {'template': 'BJN {short_name} {creator_percent}% {tail}', 'tailLength': 8})
            self.assertEqual(saved['tailLength'], 8)
            self.assertEqual(load(tmp), saved)
            self.assertEqual(json.loads(path.read_text())['template'], saved['template'])
            self.assertEqual(fingerprint(load(tmp)), fingerprint(saved))

    def test_name_for_uses_the_cached_short_name_then_falls_back_to_the_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            rendered = name_for(tmp, pid='1729480061238089885', campaign='7685262119046498070',
                                creator_percent='13', short_name='quaderni', public_percent='12', total_percent='14')
            self.assertEqual(rendered['name'], '🔥 BJN quaderni 13% ' + tail_for('1729480061238089885', '7685262119046498070', '13', DEFAULTS))
            self.assertEqual(rendered['shortName'], 'quaderni')
            # No cache database present: the title itself is used, never an empty name.
            self.assertEqual(short_name_for(tmp, '1', 'Tavolo da giardino in teak'), 'Tavolo da giardino in teak')


if __name__ == '__main__':
    unittest.main()

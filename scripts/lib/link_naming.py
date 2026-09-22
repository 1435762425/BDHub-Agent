"""Frontend-controllable naming for newly created TapLinks.

The name is the only human-visible label of a card on the platform, so the operator must be
able to change it without a code change. Two rules keep that safe:

* the template is validated before it is stored (known placeholders, platform length limit);
* the naming rule fingerprint is part of the standard-material idempotency key.  A frozen intent
  never changes, while a later naming-rule version creates a new standard card and leaves the old
  one as history.
"""
import json
import re
from pathlib import Path

from lib.second_cycle import digest

PLACEHOLDERS = {'short_name', 'creator_percent', 'public_percent', 'total_percent', 'tail', 'pid_last6', 'campaign_last6', 'market'}
DEFAULTS = {'version': 'link-naming-v1',
            'template': '🔥 BJN {short_name} {creator_percent}% {tail}',
            'tailLength': 6, 'maxLength': 50, 'shortNameMaxLength': 30}


def config_path(root, market='it'):
    return Path(root) / ('config/link-naming.json' if market == 'it' else f'config/link-naming-{market}.json')


def defaults_for(root, market='it'):
    if market == 'it':
        return dict(DEFAULTS)
    from lib.market_content import taplink_defaults
    return taplink_defaults(root, market)


def _bounded(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(code)
    return value


def validate(raw, *, market='it', root=None):
    """Normalise and check one config; raises ValueError with a stable code."""
    if not isinstance(raw, dict):
        raise ValueError('link_naming_invalid')
    defaults = defaults_for(root or Path(__file__).resolve().parents[2], market)
    if raw.get('market', market) != market:
        raise ValueError('link_naming_market_mismatch')
    template = str(raw.get('template') or defaults['template'])
    if not template.strip() or len(template) > 120:
        raise ValueError('link_naming_template_invalid')
    found = set(re.findall(r'\{([a-z_0-9]+)\}', template))
    unknown = found - PLACEHOLDERS
    if unknown:
        raise ValueError('link_naming_placeholder_unknown')
    if 'short_name' not in found:
        # Without the product short name every card of a market would look alike.
        raise ValueError('link_naming_requires_short_name')
    if template.count('{') != template.count('}'):
        raise ValueError('link_naming_template_invalid')
    config = {'version': str(raw.get('version') or defaults['version']),
            'template': template,
            'tailLength': _bounded(raw.get('tailLength', defaults['tailLength']), 4, 12, 'link_naming_tail_invalid'),
            'maxLength': _bounded(raw.get('maxLength', defaults['maxLength']), 10, 50, 'link_naming_max_invalid'),
            'shortNameMaxLength': _bounded(raw.get('shortNameMaxLength', defaults['shortNameMaxLength']), 1, 40, 'link_naming_short_invalid')}
    if market != 'it':
        config.update(market=market, language=defaults['language'], locale=defaults['locale'])
    return config


def load(root, market='it'):
    path = config_path(root, market)
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else defaults_for(root, market)
    return validate(raw, market=market, root=root)


def save(root, raw, market='it'):
    """Validate first, then write atomically: a rejected template must not touch the file."""
    config = validate(raw, market=market, root=root)
    path = config_path(root, market)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)
    return config


def fingerprint(config):
    return digest(config)


def tail_for(pid, campaign, creator_percent, config):
    return digest([str(pid), str(campaign), str(creator_percent)])[:config['tailLength']]


def render_full(config, *, short_name, creator_percent, tail, pid='', campaign='',
                public_percent=None, total_percent=None, market='it'):
    """Render one name, shrinking the short name until the platform limit is met.

    Returns the rendered name plus the short name actually used, so the frozen intent can
    record exactly what the platform card will be called.
    """
    short = str(short_name or '')[:config['shortNameMaxLength']]

    def build(short_value):
        values = {'short_name': short_value, 'creator_percent': str(creator_percent),
                  'public_percent': '' if public_percent is None else str(public_percent),
                  'total_percent': '' if total_percent is None else str(total_percent),
                  'tail': str(tail)[:config['tailLength']], 'pid_last6': str(pid)[-6:],
                  'campaign_last6': str(campaign)[-6:], 'market': str(market)}
        return ' '.join(config['template'].format(**values).split())

    name = build(short)
    while len(name) > config['maxLength'] and ' ' in short:
        short = short.rsplit(' ', 1)[0]
        name = build(short)
    while len(name) > config['maxLength'] and short:
        short = short[:-1]
        name = build(short)
    if not name or len(name) > config['maxLength']:
        raise ValueError('link_naming_too_long')
    return {'name': name, 'shortName': short, 'tail': str(tail)[:config['tailLength']]}


def render(config, **kwargs):
    return render_full(config, **kwargs)['name']


def short_name_for(root, pid, title, market='it'):
    """Cached AI short name for one product, else a trimmed title. Shared by preview and create.

    The lookup is by product id, not by the cached title. A short name belongs to the product; the
    title is only the input the model saw. Keying on the title meant that the same product arriving
    from a different source -- Kalodata writes ``cuscino per il collo morbido``, the platform listing
    writes ``Cuscino morbido per il collo`` -- silently missed the cache and fell back to a truncated
    raw title, so no generated name was ever used on a card.
    """
    import sqlite3
    from contextlib import closing
    db = Path(root) / 'var/second-cycle.sqlite'
    if db.exists():
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('BEGIN')
            from lib.market_content import market_content
            locale = market_content(root, market)['locale']
            row = conn.execute('SELECT payload FROM cycle_product_name WHERE pid=? AND locale=? '
                               'ORDER BY rowid DESC LIMIT 1', (str(pid), locale)).fetchone()
        if row:
            payload = json.loads(row[0])
            # A non-IT locale must contain its own neutral fields.  Accepting ``shortNameIt`` here
            # would quietly put an Italian product name on a Brazilian, Malaysian or UK card.
            value = payload.get('shortNameIt') if market == 'it' else payload.get('shortName')
            # The writer validates names up to 60 characters. Refusing anything over 30 here threw
            # away a perfectly good generated name; fitting it to the card limit is render_full's
            # job, and it already trims by whole words.
            if isinstance(value, str) and 1 <= len(value) <= 60:
                return value
    if market != 'it':
        raise ValueError('link_naming_localized_short_name_missing')
    cleaned = ' '.join(str(title).split())
    return (cleaned[:30].rsplit(' ', 1)[0] if len(cleaned) > 30 and ' ' in cleaned[:31] else cleaned[:30]) or str(pid)


def name_for(root, *, pid, campaign, creator_percent, short_name, public_percent=None,
             total_percent=None, market='it', config=None):
    """Convenience wrapper used by the creation path."""
    config = config or load(root, market)
    tail = tail_for(pid, campaign, creator_percent, config)
    return render_full(config, short_name=short_name, creator_percent=creator_percent, tail=tail,
                       pid=pid, campaign=campaign, public_percent=public_percent,
                       total_percent=total_percent, market=market)

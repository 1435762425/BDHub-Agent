"""BR sample cost authority, separate from platform price and review capability.

The local file contains the user's business policy. Loading or using it neither
changes application facts nor verifies the platform's approval permission.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from lib.second_cycle import CycleError, digest

POLICY_SCHEMA = 'bdhub.br-sample-cost-policy.v1'
BASIS_SCHEMA = 'bdhub.br-sample-payment-basis.v1'
POLICY_PATH = 'private-data/business-policy/br-sample-cost.json'
USER_CONFIRMATION = '商家承担费用，机构不用付款'
_KEYS = frozenset({'schema', 'market', 'payer', 'agencyPaymentAllowed', 'requestPayment',
                   'authority', 'authorityEvidence', 'confirmedAt', 'revision'})
_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})')


def validate_cost_policy(policy, *, now=None):
    """Return a detached policy snapshot with strict user authority and time."""
    if not isinstance(policy, dict) or set(policy) != _KEYS or \
            policy.get('schema') != POLICY_SCHEMA or policy.get('market') != 'br' or \
            policy.get('payer') != 'seller' or policy.get('agencyPaymentAllowed') is not False or \
            policy.get('requestPayment') != 'omit' or policy.get('authority') != 'user' or \
            policy.get('authorityEvidence') != USER_CONFIRMATION or \
            type(policy.get('revision')) is not int or not 1 <= policy['revision'] <= 2 ** 53 - 1:
        raise CycleError('sample_cost_policy_invalid')
    current = datetime.now(timezone.utc).timestamp() if now is None else now
    if type(current) not in (int, float) or not math.isfinite(current) or current <= 0:
        raise CycleError('sample_cost_policy_clock_invalid')
    confirmed = policy.get('confirmedAt')
    if not isinstance(confirmed, str) or not _TIMESTAMP.fullmatch(confirmed):
        raise CycleError('sample_cost_policy_time_invalid')
    try:
        at = datetime.fromisoformat(confirmed.replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError):
        raise CycleError('sample_cost_policy_time_invalid') from None
    if at <= 0 or at > current:
        raise CycleError('sample_cost_policy_time_invalid')
    return deepcopy(policy)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate policy field')
        value[key] = item
    return value


def load_cost_policy(root, *, now=None):
    """Read the existing private policy only; a missing file supplies no policy."""
    try:
        source = (Path(root) / POLICY_PATH).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise CycleError('sample_cost_policy_unavailable') from None
    if len(source) > 8192:
        raise CycleError('sample_cost_policy_invalid')
    try:
        policy = json.loads(source.decode('utf-8'), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError):
        raise CycleError('sample_cost_policy_invalid') from None
    return validate_cost_policy(policy, now=now)


def payment_basis(application, policy=None, *, now=None):
    """Resolve cost authority or raise; never invent or mutate a platform fact.

    Explicit platform payment requirements always block. Explicit False is a
    platform fact independent of policy. Missing payment facts need the exact
    user-confirmed seller policy. The returned snapshot and fingerprint can be
    frozen in the existing review intent; they do not authorize a review.
    """
    if not isinstance(application, dict):
        raise CycleError('sample_application_invalid')
    required = application.get('paymentRequired')
    if required is True:
        raise CycleError('sample_payment_requires_handling')
    if required is False:
        source, snapshot_policy = 'platform_fact', None
    elif required is None and policy is not None:
        source, snapshot_policy = 'user_policy', validate_cost_policy(policy, now=now)
    else:
        raise CycleError('sample_payment_requires_handling')
    snapshot = {'schema': BASIS_SCHEMA, 'market': 'br', 'source': source,
                'paymentRequired': required, 'requestPayment': 'omit', 'policy': snapshot_policy}
    return {'source': source, 'snapshot': snapshot, 'fingerprint': digest(snapshot)}

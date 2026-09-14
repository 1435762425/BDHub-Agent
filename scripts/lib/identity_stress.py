"""Frozen, bounded read-only stress-test manifests; never a production rate override."""
import json,re
from pathlib import Path

def stress_case(root,body,account):
 token=body.get('stressRun')
 if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{32}',token):raise ValueError('invalid_stress_run')
 folder=Path(root)/'var/identity-stress'/token
 manifest=json.loads((folder/'manifest.json').read_text())
 case=manifest['cases'].get(body.get('stressCase'))
 if not case or account not in case['accounts'] or body.get('identityOnly') is not True or body.get('cohortId'):raise ValueError('stress_scope_mismatch')
 if case['qps'] not in (3,5,8,12) or case['lanes'] not in (3,6,9):raise ValueError('stress_rate_invalid')
 expected=manifest['targets'][account]
 if body.get('targets')!=expected or not 1<=len(expected)<=40:raise ValueError('stress_targets_mismatch')
 return folder,case,manifest['identityFingerprints'][account]

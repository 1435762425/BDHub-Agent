#!/usr/bin/env python3
"""Offline source inventory. Never imports legacy code or reads credentials/data."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

DEFAULT = Path('/Users/bjn00003/BDHub/01-BDSystem-V2')
ENDPOINT = re.compile(r'/(?:api/v[0-9]+|v[12]/(?:message|conversation))[/A-Za-z0-9_.{}-]+')

def scan(root):
    endpoints, columns, keys, sources, errors = {}, {}, {}, {}, []
    count = 0
    for folder in ('bdhub', 'dashboard'):
        for path in sorted((root / folder).rglob('*.py')):
            rel = path.relative_to(root).as_posix()
            if 'tests' in path.parts or path.name.startswith('test_'):
                continue
            raw = path.read_bytes()
            content = raw.decode('utf-8')
            count += 1
            hits = list(ENDPOINT.finditer(content))
            try:
                tree = ast.parse(content)
            except SyntaxError:
                errors.append(rel)
                continue
            if hits:
                sources[rel] = hashlib.sha256(raw).hexdigest()
            for match in hits:
                endpoint = match.group()
                ref = {'file': rel, 'line': content.count('\n', 0, match.start()) + 1}
                if ref not in endpoints.setdefault(endpoint, []):
                    endpoints[endpoint].append(ref)
            # Candidate field names are syntax observations, NOT response schemas.
            if hits:
                fields = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
                        value = node.args[0]
                        if isinstance(value, ast.Constant) and isinstance(value.value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_:.-]{0,99}', value.value):
                            fields.add(value.value)
                keys[rel] = sorted(fields)
            if rel == 'bdhub/hub/schema.py':
                sources[rel] = hashlib.sha256(raw).hexdigest()
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'Table' or not node.args:
                        continue
                    name = node.args[0]
                    if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
                        continue
                    cols = []
                    for arg in node.args:
                        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == 'Column' and arg.args and isinstance(arg.args[0], ast.Constant):
                            cols.append({'name': arg.args[0].value, 'type': ast.unparse(arg.args[1]) if len(arg.args)>1 else None, 'line': arg.lineno})
                    columns[name.value] = cols
    return {'schema':'bdhub.tiktok.source-inventory.v1', 'scope':'Python source literals in bdhub/dashboard excluding tests; no runtime or credentials',
            'limitations':['Not an exhaustive platform endpoint list', 'String paths may be partial bases', 'Method and side effects require review', 'get keys are code candidates, not verified response fields', 'Dynamically constructed/JS bundle paths may be absent'],
            'filesScanned':count,'parseErrors':errors,'sourceSha256':sources,
            'endpoints':[{'path':p,'evidence':'source_only','references':refs} for p,refs in sorted(endpoints.items())],
            'candidateKeysByFile':keys,'databaseColumnsByTable':columns}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--legacy',type=Path,default=DEFAULT)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    target=args.output.resolve()
    root=args.legacy.resolve()
    if target.is_relative_to(root):
        p.error('output must not be inside read-only legacy project')
    result=scan(root)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'files':result['filesScanned'],'endpointLiterals':len(result['endpoints']),'tables':len(result['databaseColumnsByTable']),'parseErrors':result['parseErrors']}))

if __name__=='__main__':
    main()

#!/usr/bin/env python3
"""Rebuild vendor/bdhub from the legacy repository (I1).

Copies the transitive import closure of the legacy ``bdhub`` modules that
BDHub-Agent actually imports, so the new project can eventually run without
the sibling repository. Source-only: never copies config.yaml, secrets,
identity files or cookies.

Usage:
  python scripts/vendor-legacy-bdhub.py --check      # report drift, write nothing
  python scripts/vendor-legacy-bdhub.py --write      # refresh vendor/bdhub
"""
import argparse
import ast
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEGACY = ROOT.parent / '01-BDSystem-V2'
DEFAULT_OUT = ROOT / 'vendor'

# Modules the new project imports today. Keep in sync with `grep -rhoE "bdhub[.a-z_]*" scripts/`.
ROOTS = [
    'bdhub.account_policy',
    'bdhub.enrich.creator_profile',
    'bdhub.enrich.identity_store',
    'bdhub.enrich.profile_lease',
    'bdhub.enrich.pure_http_worker',
    'bdhub.enrich.shared_backoff',
    'bdhub.hub.engine',
    'bdhub.hub.markets',
    'bdhub.imbase.account_binding',
    'bdhub.research.catalog_rules',
    'bdhub.research.commerce_transport',
    'bdhub.research.kalodata_worker',
    'bdhub.send.http_write_gate',
    'bdhub.send.sharelink.transport',
    'bdhub.send.taplink.protocol',
    'bdhub.send.taplink.transport',
    'bdhub.send.worker',
]
FORBIDDEN = ('.yaml', '.yml', '.json', '.pem', '.key', '.crt', '.env')


def module_map(pkg: Path):
    """module name -> source path, for every .py under the legacy package."""
    out = {}
    for dp, dns, fns in os.walk(pkg):
        dns[:] = [d for d in dns if d != '__pycache__']
        for fn in fns:
            if not fn.endswith('.py'):
                continue
            path = Path(dp) / fn
            rel = path.relative_to(pkg.parent).with_suffix('')
            parts = list(rel.parts)
            if parts[-1] == '__init__':
                parts = parts[:-1]
            out['.'.join(parts)] = path
    return out


def dependencies(path: Path, module: str):
    """Module names referenced by one file, resolving relative imports."""
    package = module if path.name == '__init__.py' else module.rsplit('.', 1)[0]
    found = set()
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = package
            for _ in range(max(0, (node.level or 0) - 1)):
                base = base.rsplit('.', 1)[0] if '.' in base else base
            if node.level:
                target = f'{base}.{node.module}' if node.module else base
                found.add(target)
                found.update(f'{target}.{a.name}' for a in node.names if a.name != '*')
            elif node.module and node.module.startswith('bdhub'):
                found.add(node.module)
                found.update(f'{node.module}.{a.name}' for a in node.names if a.name != '*')
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith('bdhub'))
    return found


def closure(files):
    def resolve(name):
        if name in files:
            return [name]
        init = f'{name}.__init__'
        return [init] if init in files else []

    seen, stack = set(), list(ROOTS)
    while stack:
        for resolved in resolve(stack.pop()):
            if resolved in seen:
                continue
            seen.add(resolved)
            source = files[resolved]
            for dep in dependencies(source, resolved):
                if resolve(dep):
                    stack.append(dep)
    return seen


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--legacy', type=Path, default=DEFAULT_LEGACY)
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--write', action='store_true', help='refresh the vendored copy')
    ap.add_argument('--check', action='store_true', help='report drift only (default)')
    args = ap.parse_args()

    package = args.legacy / 'bdhub'
    if not package.is_dir():
        sys.exit(f'legacy package not found: {package}')

    files = module_map(package)
    names = closure(files)
    lines = sum(len(files[n].read_text(encoding='utf-8').splitlines()) for n in names)

    target = args.out / 'bdhub'
    print(f'legacy   : {package}')
    print(f'modules  : {len(names)}')
    print(f'lines    : {lines}')
    print(f'target   : {target}')

    # Refuse to carry anything that is not Python source.
    bad = [p for n in names for p in [files[n]] if p.suffix in FORBIDDEN]
    if bad:
        sys.exit(f'refusing to vendor non-source files: {bad}')

    if args.write:
        if target.exists():
            shutil.rmtree(target)
        for name in sorted(names):
            source = files[name]
            rel = source.relative_to(package.parent)
            dest = args.out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        # Ensure every intermediate package is importable.
        for name in sorted(names):
            rel = files[name].relative_to(package.parent)
            parts = list(rel.parent.parts)
            for i in range(1, len(parts) + 1):
                pkg_dir = args.out.joinpath(*parts[:i])
                pkg_dir.mkdir(parents=True, exist_ok=True)
                init = pkg_dir / '__init__.py'
                if not init.exists():
                    init.touch()
        print('written')
    else:
        present = {p.relative_to(args.out).as_posix() for p in target.rglob('*.py')} if target.exists() else set()
        expected = set()
        for name in names:
            rel = files[name].relative_to(package.parent)
            expected.add(rel.as_posix())
            # intermediate packages are created as empty __init__.py so the tree is importable
            parts = list(rel.parent.parts)
            for i in range(1, len(parts) + 1):
                expected.add('/'.join([*parts[:i], '__init__.py']))
        missing, extra = sorted(expected - present), sorted(present - expected)
        print(f'missing  : {len(missing)}')
        for m in missing[:10]:
            print(f'   - {m}')
        print(f'extra    : {len(extra)}')
        for e in extra[:10]:
            print(f'   + {e}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

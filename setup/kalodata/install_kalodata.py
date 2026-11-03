#!/usr/bin/env python3
"""Install the authorized Kalodata configuration in a chosen development checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import yaml

ROOT = Path(__file__).resolve().parent

def install(project, canary_pid=None):
    project = Path(project).expanduser().resolve()
    if not (project / 'scripts/kalodata-identity.py').is_file() or not (project / 'AGENTS.md').is_file():
        raise ValueError('target_is_not_a_BDHub_Agent_checkout')
    incoming = json.loads((ROOT / 'config/kalodata-identity.json').read_text())
    code = incoming.get('activationCode')
    if not isinstance(code, str) or not code.strip() or len(code) > 200 or any(c in code for c in '\r\n\t'):
        raise ValueError('activation_configuration_invalid')
    if canary_pid is not None:
        if not re.fullmatch(r'[0-9]{19}', canary_pid):
            raise ValueError('canary_pid_requires_19_digits')
        incoming['canaryPid'] = canary_pid
    collector = ROOT / 'runtime/collector'
    extension = ROOT / 'runtime/extension'
    manifest = json.loads((ROOT / 'runtime/manifest.json').read_text())
    for entry in manifest['files']:
        path = (ROOT / entry['path']).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('runtime_file_hash_mismatch')

    def checked(name):
        path = project / name
        if not path.resolve().is_relative_to(project):
            raise ValueError('target_path_outside_project')
        return path

    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    identity_path = checked('config/kalodata-identity.json')
    current = json.loads(identity_path.read_text()) if identity_path.exists() else {}
    if current.get('canaryPid') and canary_pid is None:
        incoming['canaryPid'] = current['canaryPid']
    identity = {**current, **incoming}
    config_path = checked('private-data/config.yaml')
    config = yaml.safe_load(config_path.read_text()) if config_path.exists() else yaml.safe_load((project / 'vendor/config.example.yaml').read_text())
    if not isinstance(config, dict):
        raise ValueError('project_private_config_invalid')
    section = config.get('kalodata') or {}
    if not isinstance(section, dict):
        raise ValueError('project_kalodata_config_invalid')
    config['kalodata'] = {**section, 'project_dir': str(collector)}
    lock = checked('private-data/data/research/kalodata/.browser.lock')
    environment_path = checked('.env.kalodata.local')
    environment = 'export KALODATA_PROJECT_DIR=' + shlex.quote(str(collector)) + '\n' + 'export KALODATA_EXTENSION_DIR=' + shlex.quote(str(extension)) + '\n'
    write(identity_path, json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    write(config_path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    write(environment_path, environment)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if not lock.is_file():
            raise ValueError('existing_browser_lock_is_not_a_file')
    else:
        os.close(fd)
    writable_var = checked('var')
    writable_var.mkdir(exist_ok=True)
    return {'configured': True, 'activationConfigured': True, 'project': str(project),
            'collector': str(collector), 'extension': str(extension), 'environmentFile': str(environment_path),
            'loginStarted': False, 'platformRequests': 0,
            'varIsReadOnlySnapshot': (writable_var / 'READ_ONLY_DEVELOPMENT.json').exists()}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--canary-pid')
    args = parser.parse_args()
    print(json.dumps(install(args.project, args.canary_pid), ensure_ascii=False, indent=2))

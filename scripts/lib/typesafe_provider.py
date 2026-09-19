"""Bounded TypeSafe Jev provider using the official System One HTTP contract."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from lib.second_cycle import CycleError

DEFAULT = {'version':'typesafe-provider-v1','endpoint':'https://api.typesafe.ai/v1/systemone',
           'model':'jev-1.13.0','apiKey':''}
SAFE_ERRORS = {'typesafe_not_configured','typesafe_config_invalid','typesafe_auth_rejected',
               'typesafe_rate_limited','typesafe_overloaded','typesafe_http_error',
               'typesafe_network_error','typesafe_response_invalid','typesafe_model_unavailable'}


def config_path(root):return Path(root)/'config/typesafe.json'


def load(root):
    path=config_path(root)
    raw=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(DEFAULT)
    if not isinstance(raw,dict) or set(raw)-set(DEFAULT):raise CycleError('typesafe_config_invalid')
    value={**DEFAULT,**raw};endpoint=str(value['endpoint']).strip();parsed=urlsplit(endpoint)
    key=str(os.environ.get('TYPESAFE_API_KEY') or value['apiKey']).strip();model=str(value['model']).strip()
    if value['version']!='typesafe-provider-v1' or parsed.scheme!='https' or parsed.hostname!='api.typesafe.ai' or \
            parsed.path!='/v1/systemone' or parsed.query or parsed.fragment or model!='jev-1.13.0' or \
            any(c in key for c in '\r\n\t') or len(key)>500:
        raise CycleError('typesafe_config_invalid')
    return {'version':value['version'],'endpoint':endpoint,'model':model,'apiKey':key}


def status(root):
    try:value=load(root);ready=bool(value['apiKey']);error=None if ready else 'typesafe_not_configured'
    except (OSError,ValueError,json.JSONDecodeError,CycleError):ready=False;value=DEFAULT;error='typesafe_config_invalid'
    return {'provider':'typesafe','model':value['model'],'ready':ready,'credentialReady':ready,
            'errorCode':error,'endpoint':'https://api.typesafe.ai/v1/systemone'}


def _request(root,method,url,*,payload=None,session_factory=None,timeout=(5,30)):
    config=load(root)
    if not config['apiKey']:raise CycleError('typesafe_not_configured')
    if session_factory is None:
        import requests
        session_factory=requests.Session
    try:
        with session_factory() as session:
            session.trust_env=False
            response=session.request(method,url,headers={'Authorization':'Bearer '+config['apiKey'],
                'Content-Type':'application/json','Accept':'application/json'},json=payload,timeout=timeout,
                allow_redirects=False)
            status_code=response.status_code;content=response.content
    except Exception:raise CycleError('typesafe_network_error') from None
    if len(content)>2_000_000:raise CycleError('typesafe_response_invalid')
    if status_code==401:raise CycleError('typesafe_auth_rejected')
    if status_code==429:raise CycleError('typesafe_rate_limited')
    if status_code==529:raise CycleError('typesafe_overloaded')
    if status_code!=200:raise CycleError('typesafe_http_error')
    try:value=json.loads(content)
    except (ValueError,UnicodeDecodeError):raise CycleError('typesafe_response_invalid') from None
    if not isinstance(value,dict):raise CycleError('typesafe_response_invalid')
    return value


def list_models(root,*,session_factory=None):
    value=_request(root,'GET','https://api.typesafe.ai/v1/models',session_factory=session_factory)
    rows=value.get('models')
    if not isinstance(rows,list) or any(not isinstance(row,dict) or not isinstance(row.get('name'),str) for row in rows):
        raise CycleError('typesafe_response_invalid')
    return {'models':[row['name'] for row in rows]}


def system_one(root,state,questions,*,session_factory=None):
    config=load(root)
    if not isinstance(state,(str,dict,list)) or not isinstance(questions,dict) or not questions:
        raise CycleError('typesafe_config_invalid')
    try:encoded=json.dumps({'state':state,'model':config['model'],'questions':questions},ensure_ascii=False,
                           separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError):raise CycleError('typesafe_config_invalid') from None
    if len(encoded.encode('utf-8'))>120_000:raise CycleError('typesafe_config_invalid')
    payload=json.loads(encoded)
    value=_request(root,'POST',config['endpoint'],payload=payload,session_factory=session_factory)
    if value.get('model') not in ('jev-1.13.0','jev-latest') or not isinstance(value.get('answers'),dict) or \
            not isinstance(value.get('usage'),dict):raise CycleError('typesafe_response_invalid')
    return value

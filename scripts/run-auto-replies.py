#!/usr/bin/env python3
"""Retired V1 executable; keep the old run_reply import for historical callers."""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))

from lib.cycle_send_runtime import fresh_card
from lib.second_live_runtime import _authenticated,read_sender_binding,live_runtime

STOP=False


def refresh(candidate):
    with _authenticated({},stopped=lambda:STOP) as (account,identity,headers,auth,maintenance,available):
        _,proof=fresh_card(candidate,account,identity,headers,maintenance,lambda:STOP)
    return proof


def run_reply(store,replies,reply,*,authorized_now=False,stopped=lambda:STOP):
    from lib.reply_transport import run_reply as execute_reply
    return execute_reply(store,replies,reply,root=ROOT,authorized_now=authorized_now,stopped=stopped,
                         read_binding=read_sender_binding,live=live_runtime,refresh=refresh)


def main():
    print(json.dumps({'error':'legacy_auto_reply_retired'}))
    return 2


if __name__=='__main__':raise SystemExit(main())

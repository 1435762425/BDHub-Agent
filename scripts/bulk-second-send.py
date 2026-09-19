#!/usr/bin/env python3
"""Retired legacy sender.

Historical ``cycle_bulk`` rows remain readable, but they are not execution authorization.  New
batches must be previewed, frozen and explicitly started through ``scripts/send-batch.py``.
"""
import argparse
import json

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--batch-id',required=True);parser.add_argument('--target',type=int,default=100)
 parser.add_argument('--once',action='store_true');parser.add_argument('--lanes',type=int,default=1)
 parser.parse_args(argv)
 print(json.dumps({'error':'legacy_bulk_sender_retired','requiredEntry':'send-batch.py freeze -> start'},ensure_ascii=False))
 return 2

if __name__=='__main__':raise SystemExit(main())

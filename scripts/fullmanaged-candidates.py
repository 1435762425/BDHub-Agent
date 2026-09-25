#!/usr/bin/env python3
"""Inspect cumulative full-managed candidates, or explicitly migrate local evidence after backup."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT/'scripts'))
from lib.fullmanaged_candidates import candidate_rows,migrate
from lib.market_registry import supports

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['status','migrate']);p.add_argument('--market',required=True)
    p.add_argument('--confirm',action='store_true');a=p.parse_args()
    if not supports(ROOT,a.market,'fullManagedCatalog'):p.error('market has no full-managed catalog')
    if a.action=='migrate':
        if not a.confirm:p.error('migration requires --confirm and a prior database backup')
        result=migrate(ROOT,a.market)
    else:
        rows=candidate_rows(ROOT,a.market)
        result={'market':a.market,'total':len(rows),'platformWrites':0}
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()

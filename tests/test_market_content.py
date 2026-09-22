import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from lib.cycle_materials import checked_names  # noqa:E402
from lib.link_naming import load as load_naming,short_name_for  # noqa:E402
from lib.market_content import SEND_IDS,load_content  # noqa:E402
from lib.second_cycle import CycleError  # noqa:E402


class MarketContentTests(unittest.TestCase):
 def test_every_enabled_market_has_its_exact_language_and_distinct_send_copy(self):
  content=load_content(ROOT);expected={
   'it':('it','it-IT'),'br':('pt','pt-BR'),'my':('ms','ms-MY'),'uk':('en','en-GB')}
  self.assertEqual(set(content['markets']),set(expected))
  for market,(language,locale) in expected.items():
   row=content['markets'][market]
   self.assertEqual((row['language'],row['locale']),(language,locale))
   naming=load_naming(ROOT,market)
   if market!='it':
    self.assertEqual((naming['market'],naming['language'],naming['locale']),(market,language,locale))
  for template_id in SEND_IDS:
   bodies=[content['markets'][market]['sendTemplates'][template_id]['text'] for market in expected]
   self.assertEqual(len(bodies),len(set(bodies)))

 def test_non_it_name_contract_rejects_italian_fields(self):
  inputs=[{'pid':'1','title':'Travesseiro cervical'}]
  with self.assertRaisesRegex(CycleError,'names_invalid'):
   checked_names({'items':[{'ref':'0','shortNameIt':'cuscino cervicale',
                            'mentionIt':'questo cuscino','shortNameZh':'颈枕'}]},inputs,'br')
  parsed=checked_names({'items':[{'ref':'0','shortName':'travesseiro cervical',
                                 'mention':'travesseiro cervical','shortNameZh':'颈枕'}]},inputs,'br')
  self.assertEqual((parsed['0']['language'],parsed['0']['locale']),('pt','pt-BR'))

 def test_non_it_taplink_name_never_reads_it_locale_or_it_field(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
   for name in ('markets.json','market-content.json'):
    shutil.copyfile(ROOT/'config'/name,root/'config'/name)
   db=root/'var/second-cycle.sqlite';pid='1729480061238089885'
   with closing(sqlite3.connect(db)) as conn,conn:
    conn.execute('CREATE TABLE cycle_product_name(id TEXT PRIMARY KEY,pid TEXT,locale TEXT,'
                 'source_title TEXT,payload TEXT,job_id TEXT)')
    conn.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?)',
                 ('it-row',pid,'it-IT','title',json.dumps({'shortNameIt':'cuscino cervicale'}),'job'))
   with self.assertRaisesRegex(ValueError,'link_naming_localized_short_name_missing'):
    short_name_for(root,pid,'Travesseiro cervical premium','br')
   with closing(sqlite3.connect(db)) as conn,conn:
    conn.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?)',
                 ('br-bad',pid,'pt-BR','title',json.dumps({'shortNameIt':'cuscino cervicale'}),'job'))
   with self.assertRaisesRegex(ValueError,'link_naming_localized_short_name_missing'):
    short_name_for(root,pid,'Travesseiro cervical premium','br')
   with closing(sqlite3.connect(db)) as conn,conn:
    conn.execute("UPDATE cycle_product_name SET payload=? WHERE id='br-bad'",
                 (json.dumps({'shortName':'travesseiro cervical'}),))
   self.assertEqual(short_name_for(root,pid,'Travesseiro cervical premium','br'),
                    'travesseiro cervical')


if __name__=='__main__':unittest.main()

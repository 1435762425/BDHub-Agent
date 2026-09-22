"""Validated market-language content used by naming, outreach and Agent replies."""
from __future__ import annotations

import json
import re
from pathlib import Path


SEND_IDS=("standard","brief","reconnect","video_focus","live_focus","flexible_format",
          "fresh_angle","audience_fit","commission_reminder","soft_followup","showcase_focus",
          "next_content","use_case_focus","creator_angle","open_invitation","casual_update")
AGENT_IDS=("sample_self_service","collaboration_ack","link_usage")
SEND_FIELDS=frozenset({"recipient","mention","rate"})
ZH_FIELDS=frozenset({"shortZh","rate"})


def _root(root=None):return Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]


def _fields(value):return set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}",value))


def validate_content(value,registry):
 if not isinstance(value,dict) or value.get("schemaVersion")!=1 or not isinstance(value.get("markets"),dict):
  raise ValueError("market_content_invalid")
 enabled={key for key,row in registry["markets"].items() if row["enabled"]}
 if set(value["markets"])!=enabled:raise ValueError("market_content_registry_mismatch")
 fingerprints={template_id:set() for template_id in SEND_IDS}
 for market,row in value["markets"].items():
  if not isinstance(row,dict) or set(row)!={"language","languageLabel","locale","tapLink","sendTemplates","agentTemplates"}:
   raise ValueError("market_content_invalid")
  expected=registry["markets"][market]
  if row["language"]!=expected["templateLanguage"] or row["locale"]!=expected["locale"] or \
     not isinstance(row["languageLabel"],str) or not row["languageLabel"]:
   raise ValueError("market_content_language_mismatch")
  tap=row["tapLink"]
  if not isinstance(tap,dict) or set(tap)!={"version","template","namePrompt"} or \
     not all(isinstance(tap[key],str) and tap[key].strip() for key in tap):
   raise ValueError("market_content_invalid")
  if "{short_name}" not in tap["template"] or len(tap["template"])>120 or len(tap["namePrompt"])>1200:
   raise ValueError("market_content_invalid")
  sends=row["sendTemplates"]
  if not isinstance(sends,dict) or tuple(sends)!=SEND_IDS:raise ValueError("market_content_invalid")
  for template_id,template in sends.items():
   if not isinstance(template,dict) or set(template)!={"label","description","text","translationZh"} or \
      any(not isinstance(item,str) or not item.strip() for item in template.values()):
    raise ValueError("market_content_invalid")
   if _fields(template["text"])-SEND_FIELDS or not {"mention","rate"}<=_fields(template["text"]) or \
      _fields(template["translationZh"])-ZH_FIELDS or not {"shortZh","rate"}<=_fields(template["translationZh"]):
    raise ValueError("market_content_placeholders_invalid")
   fingerprints[template_id].add(template["text"])
  agents=row["agentTemplates"]
  if not isinstance(agents,dict) or tuple(agents)!=AGENT_IDS or \
     any(not isinstance(text,str) or not text.strip() or len(text)>4000 for text in agents.values()):
   raise ValueError("market_content_invalid")
 if any(len(texts)!=len(enabled) for texts in fingerprints.values()):
  raise ValueError("market_content_cross_market_reuse")
 return value


def load_content(root=None):
 root=_root(root)
 from lib.market_registry import load_registry
 value=json.loads((root/"config/market-content.json").read_text(encoding="utf-8"))
 return validate_content(value,load_registry(root))


def market_content(root,key):
 try:return load_content(root)["markets"][key]
 except KeyError:raise ValueError("market_content_missing") from None


def taplink_defaults(root,key):
 row=market_content(root,key);tap=row["tapLink"]
 return {"version":tap["version"],"template":tap["template"],"tailLength":6,
         "maxLength":50,"shortNameMaxLength":30,"market":key,
         "language":row["language"],"locale":row["locale"]}


def name_prompt(root,key):return market_content(root,key)["tapLink"]["namePrompt"]
def send_template_map(root,key):return market_content(root,key)["sendTemplates"]
def agent_template_map(root,key):return market_content(root,key)["agentTemplates"]

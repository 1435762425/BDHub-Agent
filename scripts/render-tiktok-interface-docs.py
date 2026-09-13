#!/usr/bin/env python3
"""Render business and technical indexes from the reviewed local registry."""
import json
from pathlib import Path
B=Path(__file__).resolve().parents[1]/'docs/contracts/tiktok'
def main():
 r=json.loads((B/'endpoint-registry.json').read_text());obs=json.loads((B/'field-observations-20260913.json').read_text())
 def evidence(x):
  return '；'.join(e['market']+'：'+('成功读取' if e['status']=='observed' else '拒绝/未验收')+('（无明细）' if e.get('coverageNote') else '') for e in x.get('liveEvidence',[])) or '源码/历史脚本；未重验'
 lines=['# 接口路径目录','','与[业务用途目录](business-catalog.md)使用同一注册表生成。GET也可能写入；未知副作用不得调用。','', '| ID | 方法 | 路径 | 副作用 | 读取证据 |','|---|---|---|---|---|']
 for x in r['entries']:lines.append('| '+' | '.join([x['id'],x['method'],'`'+x['path']+'`',x['sideEffect'],evidence(x)])+' |')
 lines+=['','机器可读用途、参数、数据及来源见[注册表](endpoint-registry.json)。历史SDK候选独立见[网页脚本发现](web-discoveries.md)，不混入已验证能力。']
 (B/'endpoint-index.md').write_text('\n'.join(lines).rstrip()+'\n')
 lines=['# 按业务用途查TikTok接口','','每项说明用途、输入、能取到的数据、覆盖边界和证据。样品记录不用于二发历史样品来源、链接采用或额度解锁。','', '| 分类 | 解决的问题 |','|---|---|','| 账号与认证 | 机构、市场、IM身份与临时认证 |','| 达人身份与关系 | handle/OEC映射、画像、绑定与合作类别 |','| 货盘与选品 | 商品、活动、库存、期限、佣金条件 |','| 货盘写操作 | 加入活动、选入商品 |','| 链接与商品卡 | 准备链接、查询可发卡、维护列表 |','| IM消息 | 会话、收信、补回、发送和核验 |','| 经营报表 | 本机构归属GMV、订单、达人/PID、内容指标 |','| 样品服务 | 本机构可见样品事项；不证明二发采用 |','']
 for cat in ['账号与认证','达人身份与关系','货盘与选品','货盘写操作','链接与商品卡','IM消息','经营报表','样品服务']:
  lines+=['## '+cat,'']
  for x in r['entries']:
   if x.get('category')!=cat:continue
   lines+=['### '+x['id']+' '+x['purpose'],'','`'+x['method']+' '+x['path']+'`','', '- **输入：** '+x['inputContractSummary'],'- **可取得：** '+x['availableData'],'- **业务用途：** '+x['businessUse'],'- **不能据此判断：** '+x['notEvidenceFor'],'- **副作用：** '+x['sideEffect']+'；**证据：** '+evidence(x),'']
 lines+=['## 不是TikTok接口的能力','','Kalodata是第三方同品线索来源；60秒合并、模板、人工待办、关系调度、本地配额账本与模型调用是BDHub能力。未确认的剩余额度/重置、精确单次链接采用等见[缺口清单](findings-and-backlog.md)。']
 (B/'business-catalog.md').write_text('\n'.join(lines).rstrip()+'\n')
 total=sum(len(q.get('fields',{})) for q in obs['requests'])
 lines=['# 实际响应字段字典','',f'累计{len(obs["requests"])}次请求，{total}条按请求计的字段路径观察；不是唯一业务字段数。只保留路径、类型及出现次数，不保留实际业务值。空数组不证明字段不存在，code0不证明明细兼容。','']
 for i,q in enumerate(obs['requests'],1):
  lines+=['## '+str(i)+' '+q['market'].upper()+' / '+q.get('name',q['path'].split('/')[-1]),'',q['method']+' `'+q['path']+'`；'+q['observedAt']+'；'+q['status']+'。',q.get('coverageNote') or '单次/单页样本。','', '| 字段路径 | 类型及次数 |','|---|---|']
  lines+=['| `'+k+'` | '+', '.join(t+':'+str(n) for t,n in v.items())+' |' for k,v in q.get('fields',{}).items()]
  if not q.get('fields'):lines+=['| 未取得成功字段样本 | 不用错误响应推导正常合同 |']
  lines+=['']
 (B/'field-dictionary.md').write_text('\n'.join(lines).rstrip()+'\n')
 print('Rendered business-catalog, endpoint-index, field-dictionary')
if __name__=='__main__':main()

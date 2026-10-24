import {test} from 'node:test';import assert from 'node:assert/strict';
import {opsAlertKind,opsAlertSummary} from '../src/features/shell/alert-kind.ts';
test('technical faults, human decisions and normal waiting stay in separate groups',()=>{
 const kind=(id,title='')=>opsAlertKind({id,title});
 for(const id of ['scheduler-down','it-inbox-error','it-inbox-stale','it-read-failed','runtime-version-mixed','model-service-paused','br-agent-failed','it-send-unknown'])assert.equal(kind(id),'system',id);
 for(const id of ['it-human','uk-acc4-needs-human','my-acc5-identity-overdue','it-selection-isolated','my-agent-pilot-complete','br-agent-first-send'])assert.equal(kind(id),'people',id);
 for(const id of ['br-unread','it-unread'])assert.equal(kind(id),'backlog',id);
 for(const id of ['offsite-stale','restore-drill-blocked'])assert.equal(kind(id),'maintenance',id);
 for(const id of ['uk-platform-rejected','it-send-quarantined','it-inbox-stopped','production-paused'])assert.equal(kind(id),'waiting',id);
 assert.equal(kind('it-stage-catalog','IT 货盘：失败'),'system');
 assert.equal(kind('it-stage-catalog','IT 货盘：需要人工'),'people');
 assert.equal(kind('new-unrecognised-fault'),'system');
});

test('reminder summary counts market groups without adding backlog people to human tasks',()=>{
 const row=(id,market=null)=>({id,market,title:id});
 assert.equal(opsAlertSummary([row('br-human','br'),row('br-unread','br'),row('it-human','it'),row('it-unread','it'),row('offsite-stale')]),'人工事项 2 个市场 · 来信积压 2 个市场 · 维护 1 组');
 assert.equal(opsAlertSummary([row('br-agent-failed','br'),row('br-send-quarantined','br')]),'运行异常 1 组');
});

import {test} from 'node:test';import assert from 'node:assert/strict';
import {opsAlertKind} from '../src/features/shell/alert-kind.ts';
test('technical faults, human decisions and normal waiting stay in separate groups',()=>{
 const kind=(id,title='')=>opsAlertKind({id,title});
 for(const id of ['scheduler-down','it-inbox-error','it-inbox-stale','it-read-failed','runtime-version-mixed','model-service-paused','offsite-stale','restore-drill-blocked','br-agent-failed','it-send-unknown'])assert.equal(kind(id),'system',id);
 for(const id of ['it-human','br-unread','uk-acc4-needs-human','my-acc5-identity-overdue','it-selection-isolated','my-agent-pilot-complete','br-agent-first-send'])assert.equal(kind(id),'people',id);
 for(const id of ['uk-platform-rejected','it-send-quarantined','it-inbox-stopped','production-paused'])assert.equal(kind(id),'waiting',id);
 assert.equal(kind('it-stage-catalog','IT 货盘：失败'),'system');
 assert.equal(kind('it-stage-catalog','IT 货盘：需要人工'),'people');
 assert.equal(kind('new-unrecognised-fault'),'system');
});

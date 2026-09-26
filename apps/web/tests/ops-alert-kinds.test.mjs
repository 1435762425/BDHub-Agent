import {test} from 'node:test';import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
// The bar's grouping is derived from alert ids; keep the patterns honest against the backend ids.
const source=readFileSync(new URL('../src/features/shell/OpsAlertBar.tsx',import.meta.url),'utf8');
const pick=name=>new RegExp(source.match(new RegExp(`const ${name}=/(.*)/;`))[1]);
const PEOPLE=pick('PEOPLE'),WAITING=pick('WAITING');
const kind=id=>WAITING.test(id)?'waiting':PEOPLE.test(id)?'people':'system';
test('alert ids fall into system, people and waiting groups',()=>{
 for(const id of ['scheduler-down','it-inbox-error','it-inbox-stale','it-read-failed','runtime-version-mixed','model-service-paused','offsite-stale','restore-drill-blocked'])assert.equal(kind(id),'system',id);
 for(const id of ['it-human','br-unread','uk-acc4-needs-human','my-acc5-identity-overdue','it-send-unknown','it-stage-catalog','it-selection-isolated','my-agent-pilot-complete'])assert.equal(kind(id),'people',id);
 for(const id of ['uk-platform-rejected','it-send-quarantined','it-inbox-stopped','production-paused'])assert.equal(kind(id),'waiting',id);
});

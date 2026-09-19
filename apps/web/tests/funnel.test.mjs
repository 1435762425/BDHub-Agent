import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildFunnel} from '../src/features/catalog/funnel.ts';

test('the four counts read as one line from collection to leads',()=>{
 const stages=buildFunnel({collected:10000,screened:2291,screenedOf:10000,linked:1410,leadsPending:255});
 assert.deepEqual(stages.map(s=>s.key),['card-collect','card-screen','card-links','card-leads']);
 assert.deepEqual(stages.map(s=>s.value),[10000,2291,1410,255]);
 assert.equal(stages[1].hint,'合格率 22.9%');
});

test('an unread source shows nothing rather than a fabricated zero',()=>{
 const stages=buildFunnel({collected:null,screened:null,screenedOf:null,linked:null,leadsPending:null});
 assert.deepEqual(stages.map(s=>s.value),[null,null,null,null]);
 // With no denominator there is no rate to claim.
 assert.equal(stages[1].hint,'按门槛筛选');
});

test('a real zero is kept as zero',()=>{
 const stages=buildFunnel({collected:0,screened:0,screenedOf:0,linked:0,leadsPending:0});
 assert.deepEqual(stages.map(s=>s.value),[0,0,0,0]);
 assert.equal(stages[1].hint,'按门槛筛选');
});

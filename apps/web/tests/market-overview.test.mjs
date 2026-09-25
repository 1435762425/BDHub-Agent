import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateOverview,createHandlers,cachedReader} from '../src/server/market-overview/bridge.ts';
const counts={resolved:2,notFound:1,queued:0,blocked:1,noRecord:1,conflict:0};
const metric=(key,value)=>({key,label:key,value,unit:'条',note:''});
const panels=['inventory','identity','video','pool','handling','inbox'].map(id=>({id,title:id,available:false,reason:'missing',source:'ledger',observedAt:null,metrics:[]}));
panels[1]={...panels[1],available:true,reason:null,observedAt:1,metrics:[metric('rows',5),metric('handles',5)],rows:counts,handles:counts,labels:Object.fromEntries(Object.keys(counts).map(k=>[k,k]))};
const request=url=>new Request(url,{headers:{host:new URL(url).host}});
const keys=['cards','texts','creators','unconfirmed','replies','showcase','ourMessages','autoReplies','casesOpened'];
const days=Array.from({length:7},(_,i)=>({date:`2026-09-${19+i}`,...Object.fromEntries(keys.map(k=>[k,1]))}));
const sample=(market='it')=>({schemaVersion:'bdhub.market-overview.v1',market,checkedAt:100,available:true,planState:'active',panels:structuredClone(panels),waits:[],activity:{available:true,days:structuredClone(days),totals:Object.fromEntries(keys.map(k=>[k,7]))},readOnly:true,platformWrites:0,realSends:0});
test('four market decoders preserve unavailable rather than zero',()=>{for(const market of ['it','br','my','uk']){const v=validateOverview(sample(market),market);assert.equal(v.panels[0].available,false);assert.equal(v.panels[1].rows.notFound,1);}});
test('refuses cross market, writes, broken identity partition and daily totals',()=>{
 assert.throws(()=>validateOverview(sample(),'br'));
 for(const key of ['platformWrites','realSends'])assert.throws(()=>validateOverview({...sample(),[key]:1},'it'));
 const broken=sample();broken.panels[1].rows.resolved=3;assert.throws(()=>validateOverview(broken,'it'));
 const daily=sample();daily.activity.totals.creators=8;assert.throws(()=>validateOverview(daily,'it'));
 const repeated=sample();repeated.activity.days[1].date=repeated.activity.days[0].date;assert.throws(()=>validateOverview(repeated,'it'));
});
test('GET is local, explicit market scoped, no extra query parameters; errors are stable',async()=>{
 let called=0;const handler=createHandlers(async market=>{called++;return sample(market);}).GET;
 for(const [url,status] of [['http://evil.test/api/market-overview?market=it',403],['http://localhost:5198/api/market-overview',400],['http://localhost:5198/api/market-overview?market=it&market=br',400],['http://localhost:5198/api/market-overview?market=be',400],['http://localhost:5198/api/market-overview?market=it&run=1',400]])assert.equal((await handler(request(url))).status,status);
 assert.equal(called,0);assert.equal((await handler(request('http://localhost:5198/api/market-overview?market=uk'))).status,200);
 const failed=createHandlers(async()=>{throw Error('private traceback');}).GET;
 const response=await failed(request('http://localhost:5198/api/market-overview?market=it'));assert.deepEqual(await response.json(),{error:'market_overview_unavailable'});
});
test('cache shares only the same market and expires without serving failures as zero',async()=>{
 let now=0,calls=0;const read=cachedReader(async market=>{calls++;return sample(market);},()=>now);
 const values=await Promise.all([read('it'),read('it'),read('br')]);assert.equal(calls,2);assert.equal(values[2].market,'br');
 await read('it');assert.equal(calls,2);now=30001;await read('it');assert.equal(calls,3);
});

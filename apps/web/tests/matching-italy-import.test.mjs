import test from "node:test";
import assert from "node:assert/strict";
import {decimalToHundredths,planItalyImport} from "../src/server/matching/italy-import.ts";
import {MatchingStore} from "../src/server/matching/store.ts";
import {evaluateItalyReplay} from "../src/server/matching/italy-evaluation.ts";

const observed="2026-09-10T04:58:55+00:00",importedAt=Date.parse("2026-09-11T00:00:00Z");
const pid="1729779362302171335",secondPid="1729480019490150432",externalId="7047140782986101765";
function sources() {
  const file=(ref,data)=>({ref:`legacy:${ref}`,path:`research/${ref}.json`,sha256:"a".repeat(64),data});
  return {
    pilot:file("pilot",{market:"it",observed_at:observed,lead_run_id:"kd_test",window:{start:"2026-08-26",end:"2026-09-08"},rows:[{pid,label:"试验商品",price:"11.03",currency:"EUR",platform_commission:"15",public_commission:"10",free_sample_badge:false,stock:null,campaign_end_date:null}]}),
    request:file("request",{run_id:"kd_test",query:{market:"it",mode:"pid",currency:"EUR",targets:[pid],start_date:"2026-08-26",end_date:"2026-09-08"}}),
    result:file("result",{state:"completed",finished_at:observed,items:[{target:pid,status:"success",truncated:true}],rows:[{pid,handle:"fixture_creator",kalodata_creator_id:externalId,product_title:"Prodotto di prova",sale:173,channel:"video",matched:null,revenue:"Rp4,279.79万",contact:"NEVER_COPY_CONTACT",avatar:"https://example.invalid/NEVER_COPY_AVATAR",cookie:"NEVER_COPY_COOKIE"}]})
  };
}
test("Italy source adapter preserves exact IDs, unknown controls and original observation time",()=>{
  const s=sources(),plan=planItalyImport(s,importedAt),p=plan.batch.products[0],c=plan.batch.creators[0],e=plan.batch.evidence[0];
  assert.equal(p.pid,pid);assert.equal(p.priceMinor,1103);assert.equal(p.currency,"EUR");assert.equal(p.source.observedAt,Date.parse(observed));assert.notEqual(p.source.observedAt,importedAt);
  assert.equal(c.oecId,null);assert.deepEqual(c.externalIdentity,{namespace:"kalodata",id:externalId});assert.equal(c.control,"unknown");assert.equal(c.marketingStopped,null);assert.equal(c.priceMinMinor,null);assert.deepEqual(c.categories,[]);assert.deepEqual(c.formats,[]);
  assert.equal(e.units,173);assert.equal(e.format,"video");assert.equal(new Date(e.source.windowEnd).toISOString().slice(0,10),"2026-09-08");assert.equal(e.source.windowBasis,"calendar_date_unknown_timezone");assert.equal(plan.report.window.timezone,"unspecified_by_source");
  assert.equal(plan.batch.offers.length,0);assert.equal(plan.report.historicalCommercialObservations[0].creatorQuoteBps,null);assert.equal(plan.report.historicalCommercialObservations[0].platformDisplayedCommissionBps,1500);assert.equal(plan.report.historicalCommercialObservations[0].executable,false);
  const packed=JSON.stringify(plan);for(const forbidden of ["NEVER_COPY_CONTACT","NEVER_COPY_AVATAR","NEVER_COPY_COOKIE","Rp4,279.79万"])assert(!packed.includes(forbidden));
  assert.equal(plan.dataset.mode,"imported-offline");assert.equal(plan.report.counts.rejected,0);
});
test("money conversion is exact and refuses ambiguous symbols, units or floats",()=>{
  assert.equal(decimalToHundredths("39.05"),3905);assert.equal(decimalToHundredths("0"),0);assert.equal(decimalToHundredths("0.1"),10);
  for(const value of [11.03,null,"11,03","€11.03","Rp1000","1K","1.001","9007199254740992","-1",""])assert.throws(()=>decimalToHundredths(value));
});
test("completed exact-PID source scope and matching windows are mandatory",()=>{
  for(const mutate of [s=>s.request.data.query.market="mx",s=>s.request.data.query.mode="creator",s=>s.result.data.state="running",s=>s.pilot.data.lead_run_id="another",s=>s.pilot.data.window.end="2026-09-07",s=>s.result.data.items[0].status="failed",s=>s.request.data.query.targets.push(pid)]) {
    const s=sources();mutate(s);assert.throws(()=>planItalyImport(s,importedAt));
  }
});
test("unsupported currencies, numeric identities, zero units and orphan PIDs cannot become accepted facts",()=>{
  for(const mutate of [s=>s.pilot.data.rows[0].currency="IDR",s=>s.result.data.rows[0].kalodata_creator_id=7047140782986101765,s=>s.result.data.rows[0].sale=0,s=>s.result.data.rows[0].pid=secondPid]) {
    const s=sources();mutate(s);assert.throws(()=>planItalyImport(s,importedAt),/source_empty/);
  }
  const s=sources();s.result.data.rows.push({...s.result.data.rows[0],pid:secondPid});const p=planItalyImport(s,importedAt);assert.equal(p.report.counts.evidence,1);assert.equal(p.report.counts.rejected,1);
});
test("same creator across two products is one relationship and one bounded offline packet",t=>{
  const s=sources();s.pilot.data.rows.push({...s.pilot.data.rows[0],pid:secondPid,price:"39.05"});s.request.data.query.targets.push(secondPid);s.result.data.items.push({target:secondPid,status:"success"});s.result.data.rows.push({...s.result.data.rows[0],pid:secondPid,sale:2,channel:"live"});
  const plan=planItalyImport(s,importedAt);assert.equal(plan.report.counts.creators,1);assert.equal(plan.report.counts.evidence,2);
  const store=new MatchingStore(":memory:",{dataset:plan.dataset});t.after(()=>store.close());store.upsert(plan.batch);
  const report=evaluateItalyReplay(store,plan.batch);assert.equal(report.second.productReturnedPairs,2);assert.equal(report.second.creatorReturnedPairs,2);assert.equal(report.second.unexpectedPairs,0);assert.equal(report.reviewPackets.candidateHistogram["2"],1);assert.equal(report.reviewPackets.executionBlocked,true);assert.equal(report.firstQuality,"not_evaluated_missing_profiles");assert.equal(report.modelCalls,0);
  const creator=plan.batch.creators[0],run=store.recall({direction:"creator",subjectId:creator.id,source:"second",limit:50}),packet=store.prepareReview(run.id,creator.id);
  assert.equal(packet.payload.creator.source.windowBasis,"calendar_date_unknown_timezone");assert.equal(packet.payload.candidates[0].exactObservation.source.windowBasis,"calendar_date_unknown_timezone");
  assert(!JSON.stringify(report).includes("fixture_creator"));assert(!JSON.stringify(report).includes(externalId));
});
test("source fingerprints distinguish dataset versions without relying on import time",()=>{
  const s=sources(),one=planItalyImport(s,importedAt),two=planItalyImport(s,importedAt+1000);assert.equal(one.dataset.id,two.dataset.id);
  s.result.sha256="b".repeat(64);assert.notEqual(one.dataset.id,planItalyImport(s,importedAt).dataset.id);
});

import test from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {enrichItalyProfileSignals} from "../src/server/matching/italy-profile-signals.ts";

const observedAt=Date.parse("2026-07-30T08:00:00Z"),cohort="c".repeat(64),oec="7999999999999999991";
function canonical(v){if(Array.isArray(v))return `[${v.map(canonical).join(",")}]`;if(v&&typeof v==="object")return `{${Object.entries(v).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,x])=>`${JSON.stringify(k)}:${canonical(x)}`).join(",")}}`;return JSON.stringify(v);}
function seal(doc){doc.provenance.recordsSha256=createHash("sha256").update(canonical(doc.records)).digest("hex");return doc;}
function fixture(){
  const source={ref:"fixture:old",observedAt,windowStart:null,windowEnd:null};
  const creator={id:"it-profile-oec-"+oec,oecId:oec,market:"it",name:"@fixture",avatar:"",categories:["家具"],categoryFact:{status:"historical",namespace:"it-history-top-label-v1",sourceLabels:["家具"],source,transformVersion:null,timeBasis:"field_observation",note:""},formats:[],bio:"",priceMinMinor:null,priceMaxMinor:null,currency:"EUR",control:"unknown",marketingStopped:null,source};
  const doc=seal({schemaVersion:1,market:"it",records:[{oecId:oec,capturedAt:"2026-07-30T08:00:00Z",rawSnapshotId:"snapshot-1",followers:1200,unitsSold:25,avgViews:0,gmv:{value:"123.45",symbol:"€",currency:"EUR",period:null},categories:[{categoryId:"1",name:"Furniture",weight:"0.5"},{categoryId:"2",name:"Textiles & Soft Furnishings",weight:"0.3"},{categoryId:"-1",name:null,weight:"0.2"}],secret:"NEVER_COPY"}],provenance:{cohortSha256:cohort,sourceTable:"creator_profile_snapshot",transactionReadOnly:true,salesPerformanceEndTimeUnixSeconds:{1785196800:1}}});
  return {batch:{creators:[creator]},doc};
}
test("original multi-category signals expand beyond main category and preserve zero and unknown distinctions",()=>{
  const {batch,doc}=fixture(),result=enrichItalyProfileSignals(batch,doc,cohort),c=result.batch.creators[0];
  assert.deepEqual(c.categories,["家具","家纺布艺"]);assert.equal(c.profileSignals.unitsSold,25);assert.equal(c.profileSignals.avgViews,0);assert.equal(c.profileSignals.gmvValue,"123.45");assert.equal(c.profileSignals.gmvCurrency,"EUR");assert.match(c.profileSignals.periodLabel,/起始日未提供/);assert(!c.profileSignals.periodLabel.includes("30天"));
  assert.equal(c.source.observedAt,observedAt);assert.equal(c.control,"unknown");assert.equal(c.marketingStopped,null);assert.equal(result.report.namedCategoryEdges,2);assert.equal(result.report.unknownCategoryEdges,1);assert.equal(result.report.zeroAverageViews,1);
  assert(!JSON.stringify(result).includes("NEVER_COPY"));assert(!c.categories.includes("-1"));assert.equal(c.priceMinMinor,null);assert.deepEqual(c.formats,[]);
});
test("all named categories are retained without inheriting old first-item or ranking output",()=>{
  const {batch,doc}=fixture();doc.records[0].categories=[{categoryId:"3",name:"Modest Fashion",weight:"0.2"},{categoryId:"2",name:"Textiles & Soft Furnishings",weight:"0.8"}];doc.records[0].priority_score=999;
  const r=enrichItalyProfileSignals(batch,seal(doc),cohort);assert.deepEqual(r.batch.creators[0].categories,["Modest Fashion","家纺布艺"]);assert(!r.batch.creators[0].categories.includes("穆斯林时尚"));assert.equal(r.batch.creators[0].profileSignals.unitsSold,25);
});
test("hash, cohort, observation and exact identity binding reject accidental source mixing",()=>{
  for(const change of [d=>d.provenance.cohortSha256="wrong",d=>d.records[0].oecId="1",d=>d.records[0].oecId=7999999999999999991,d=>d.records[0].capturedAt="2026-07-31T08:00:00Z",d=>d.records.push(d.records[0]),d=>d.market="mx",d=>d.provenance.transactionReadOnly=false]){const {batch,doc}=fixture();change(doc);assert.throws(()=>enrichItalyProfileSignals(batch,seal(doc),cohort));}
  const {batch,doc}=fixture();doc.records[0].followers=999;assert.throws(()=>enrichItalyProfileSignals(batch,doc,cohort),/fingerprint/);
});
test("ambiguous category and money semantics are never manufactured",()=>{
  for(const change of [d=>d.records[0].gmv.currency="USD",d=>d.records[0].gmv.symbol="$",d=>d.records[0].gmv.period="30d",d=>d.records[0].categories[0].name="unmapped",d=>d.records[0].categories[0].weight="-0.1",d=>d.records[0].categories.pop(),d=>d.records[0].unitsSold=-1,d=>d.provenance.salesPerformanceEndTimeUnixSeconds={1785196800:1,1785283200:1}]){const {batch,doc}=fixture();change(doc);assert.throws(()=>enrichItalyProfileSignals(batch,seal(doc),cohort));}
});
test("missing performance remains unknown and is never invented from followers or GMV",()=>{
  const {batch,doc}=fixture();doc.records[0].unitsSold=null;doc.records[0].avgViews=null;
  const c=enrichItalyProfileSignals(batch,seal(doc),cohort).batch.creators[0];assert.equal(c.profileSignals.unitsSold,null);assert.equal(c.profileSignals.avgViews,null);assert.equal(c.profileSignals.followers,1200);
});

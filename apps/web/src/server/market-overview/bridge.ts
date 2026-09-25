import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {operationalMarket} from "../markets/registry.ts";
import {singleflight} from "../runtime/singleflight.ts";

export type Metric={key:string;label:string;value:number|null;unit:string;note:string};
export type OverviewPanel={id:string;title:string;available:boolean;reason:string|null;source:string;observedAt:number|null;metrics:Metric[];note?:string;rows?:Record<string,number>;handles?:Record<string,number>;labels?:Record<string,string>};
export type Day={date:string;cards:number;texts:number;creators:number;unconfirmed:number;replies:number;showcase:number;ourMessages:number;autoReplies:number;casesOpened:number};
export type MarketOverview={schemaVersion:"bdhub.market-overview.v1";market:string;checkedAt:number;available:boolean;planState:string|null;panels:OverviewPanel[];waits:Array<{id:string;label:string;state:string;observedAt:number|null}>;waitsUnavailable?:boolean;activity:{available:boolean;days:Day[];totals:Record<string,number>};readOnly:true;platformWrites:0;realSends:0};
const fail=():never=>{throw Error("invalid_market_overview");};
const object=(v:unknown)=>v&&typeof v==="object"&&!Array.isArray(v)?v as Record<string,unknown>:fail();
const text=(v:unknown,max=300)=>typeof v==="string"&&v.length<=max?v:fail();
const count=(v:unknown)=>typeof v==="number"&&Number.isSafeInteger(v)&&v>=0?v:fail();
const stamp=(v:unknown)=>v===null?null:typeof v==="number"&&Number.isFinite(v)&&v>=0?v:fail();
const boolean=(v:unknown)=>typeof v==="boolean"?v:fail();
const states=["resolved","notFound","queued","blocked","noRecord","conflict"];
const dayKeys=["cards","texts","creators","unconfirmed","replies","showcase","ourMessages","autoReplies","casesOpened"] as const;
export function validateOverview(raw:unknown,market:string):MarketOverview{
 const v=object(raw);
 if(v.schemaVersion!=="bdhub.market-overview.v1"||v.market!==market||!operationalMarket(market)||v.readOnly!==true||v.platformWrites!==0||v.realSends!==0)fail();
 if(!Array.isArray(v.panels)||v.panels.length>6||!Array.isArray(v.waits)||v.waits.length>20)fail();
 const panels=(v.panels as unknown[]).map(rawPanel=>{
  const p=object(rawPanel);if(!Array.isArray(p.metrics)||p.metrics.length>24)fail();
  const metrics=(p.metrics as unknown[]).map(rawMetric=>{const m=object(rawMetric);return {key:text(m.key,60),label:text(m.label,60),value:m.value===null?null:count(m.value),unit:text(m.unit,16),note:text(m.note)};});
  if(new Set(metrics.map(m=>m.key)).size!==metrics.length)fail();
  const result:OverviewPanel={id:text(p.id,32),title:text(p.title,60),available:boolean(p.available),reason:p.reason===null?null:text(p.reason),source:text(p.source),observedAt:stamp(p.observedAt),metrics};
  if(p.note!==undefined)result.note=text(p.note,500);
  if(!result.available&&metrics.length)fail();
  if(result.id==="identity"&&result.available){
   const rows=object(p.rows),handles=object(p.handles),labels=object(p.labels);
   result.rows=Object.fromEntries(states.map(k=>[k,count(rows[k])]));
   result.handles=Object.fromEntries(states.map(k=>[k,count(handles[k])]));
   result.labels=Object.fromEntries(states.map(k=>[k,text(labels[k],60)]));
   for(const [key,values] of [["rows",result.rows],["handles",result.handles]] as const){
    if(metrics.find(m=>m.key===key)?.value!==Object.values(values).reduce((a,b)=>a+b,0))fail();
   }
  }
  return result;
 });
 const expected=["inventory","identity","video","pool","handling","inbox"];
 if(new Set(panels.map(p=>p.id)).size!==panels.length||panels.some(p=>!expected.includes(p.id))||v.available===true&&panels.length!==6)fail();
 const activity=object(v.activity);if(!Array.isArray(activity.days)||activity.days.length>7)fail();
 const days=(activity.days as unknown[]).map(rawDay=>{const d=object(rawDay),date=text(d.date,10);if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||new Date(date+"T00:00:00Z").toISOString().slice(0,10)!==date)fail();return {date,...Object.fromEntries(dayKeys.map(k=>[k,count(d[k])]))} as Day;});
 if(days.some((d,i)=>i>0&&d.date<=days[i-1].date))fail();
 const totals:Record<string,number>={};
 if(boolean(activity.available)){
  if(days.length!==7)fail();const source=object(activity.totals);
  for(const key of dayKeys){totals[key]=count(source[key]);if(totals[key]!==days.reduce((n,d)=>n+d[key],0))fail();}
 }else if(days.length)fail();
 return {schemaVersion:"bdhub.market-overview.v1",market,checkedAt:stamp(v.checkedAt)??fail(),available:boolean(v.available),planState:v.planState===null?null:text(v.planState,40),panels,
  waits:(v.waits as unknown[]).map(rawWait=>{const w=object(rawWait);return {id:text(w.id,60),label:text(w.label,100),state:text(w.state,80),observedAt:stamp(w.observedAt)};}),
  waitsUnavailable:v.waitsUnavailable===true,activity:{available:activity.available as boolean,days,totals},readOnly:true,platformWrites:0,realSends:0};
}
function run(market:string):Promise<MarketOverview>{const root=projectRoot();return new Promise((resolve,reject)=>{
 execFile(join(root,".venv/bin/python"),[join(root,"scripts/market-overview.py"),"--market",market],
 {cwd:root,timeout:30000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},(error,out)=>{
  try{if(error)throw Error("market_overview_unavailable");resolve(validateOverview(JSON.parse(out),market));}catch{reject(Error("market_overview_unavailable"));}
 });
});}
export function cachedReader(reader:(market:string)=>Promise<MarketOverview>,clock=Date.now){
 const cache=new Map<string,{expires:number;value:MarketOverview}>();
 return (market:string):Promise<MarketOverview>=>{
  const old=cache.get(market);if(old&&old.expires>clock())return Promise.resolve(old.value);
  return singleflight(`market-overview:${market}`,async()=>{const value=await reader(market);cache.set(market,{expires:clock()+30000,value});return value;});
 };
}
export const readOverview=cachedReader(run);
export function createHandlers(read=readOverview){return {GET:async(request:Request)=>{
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
 const url=new URL(request.url),market=url.searchParams.get("market");
 if([...url.searchParams.keys()].some(k=>k!=="market")||url.searchParams.getAll("market").length!==1||!market||!operationalMarket(market))return Response.json({error:"invalid_query"},{status:400,headers});
 try{return Response.json(await read(market),{headers});}catch{return Response.json({error:"market_overview_unavailable"},{status:503,headers});}
}};}

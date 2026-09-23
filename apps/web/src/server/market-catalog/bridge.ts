import {execFile} from "node:child_process";
import {join} from "node:path";
import {projectRoot} from "../runtime/project-root.ts";
import {operationalMarket} from "../markets/registry.ts";
import {isLocalRequest} from "../runtime/validation.ts";
import {singleflight} from "../runtime/singleflight.ts";

export type MarketCatalogState={
 schemaVersion:"bdhub.market-catalog.v1";market:string;label:string;
 capabilities:{campaignCatalog:boolean;fullManagedCatalog:boolean};account:string;
 accountCapabilities:Record<string,string>;campaign:Record<string,unknown>;
 screen:Record<string,unknown>;fullManaged:Record<string,unknown>;
 downstream:{leadEdges:number;identityResolved:number;deliveriesConfirmed:number;activeTapLinks:number;
  activeSelectedTapLinks:number;activeCampaignTapLinks:number;currentSelectedCatalogOffers:number|null;
  currentLeadEdges:number|null;currentResolvedLeadEdges:number|null;currentResolvedCreators:number|null};
 readOnly:true;platformWrites:0;realSends:0;
};
export type MarketProductRow={
 pid:string;title:string;listedSelected:boolean|null;publicCommissionRaw:string|null;
 totalCommissionRaw:string|null;detailsChecked:boolean;stockChecked:boolean;
 selectionObservation:{state:string;observedAt:number}|null;
 selectedOffers:Array<{creatorPercent:string|null;eligible:boolean}>;
};
export type MarketProductsState={
 market:string;scope:"current"|"category"|"weekly";availability:"ready"|"unsupported"|"unavailable";items:MarketProductRow[];
 total:number;offset:number;limit:30;displayRunId:string;latestRunId:string;
 observedAt:number|null;readOnly:true;platformWrites:0;
};

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
const cleanMarket=(value:unknown)=>{
 if(typeof value!=="string"||!operationalMarket(value))throw Error("market_invalid");
 return value;
};
function run<T>(script:string,args:string[],market:string,validate:(value:unknown,market:string)=>T,timeout=60_000):Promise<T>{
 const root=projectRoot();
 return new Promise((resolve,reject)=>execFile(join(root,".venv/bin/python"),[join(root,"scripts",script),...args],
  {cwd:root,timeout,maxBuffer:4*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},
  (error,stdout)=>{try{const value=JSON.parse(stdout);if(error||value?.error)throw Error();resolve(validate(value,market));}catch{reject(Error("market_catalog_unavailable"));}}));
}
function validateCatalog(raw:unknown,market:string):MarketCatalogState{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("market_catalog_invalid");
 const value=raw as Record<string,unknown>,cap=value.capabilities as Record<string,unknown>,down=value.downstream as Record<string,unknown>,definition=operationalMarket(market);
 if(!definition||value.schemaVersion!=="bdhub.market-catalog.v1"||value.market!==market||value.label!==definition.label||value.account!==definition.accounts.supply||
  !cap||cap.campaignCatalog!==true||cap.fullManagedCatalog!==definition.capabilities.fullManagedCatalog||
  !value.accountCapabilities||typeof value.accountCapabilities!=="object"||!value.campaign||typeof value.campaign!=="object"||
  !value.screen||typeof value.screen!=="object"||!value.fullManaged||typeof value.fullManaged!=="object"||!down||
  ["leadEdges","identityResolved","deliveriesConfirmed","activeTapLinks","activeSelectedTapLinks","activeCampaignTapLinks"].some(key=>typeof down[key]!=="number"||!Number.isSafeInteger(down[key])||Number(down[key])<0)||
  ["currentSelectedCatalogOffers","currentLeadEdges","currentResolvedLeadEdges","currentResolvedCreators"].some(key=>down[key]!==null&&(!Number.isSafeInteger(down[key])||Number(down[key])<0))||
  value.readOnly!==true||value.platformWrites!==0||value.realSends!==0)throw Error("market_catalog_invalid");
 return value as unknown as MarketCatalogState;
}
function validateProducts(raw:unknown,market:string,scope:MarketProductsState["scope"]):MarketProductsState{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw Error("market_products_invalid");
 const value=raw as MarketProductsState;
 if(value.market!==market||value.scope!==scope||value.availability!=="ready"||!Array.isArray(value.items)||value.items.length>30||
  !Number.isSafeInteger(value.total)||value.total<0||!Number.isSafeInteger(value.offset)||value.offset<0||
  value.limit!==30||typeof value.displayRunId!=="string"||!value.displayRunId||
  typeof value.latestRunId!=="string"||!value.latestRunId||value.readOnly!==true||value.platformWrites!==0||
  value.items.some(row=>!/^[0-9]{19}$/.test(row.pid)||typeof row.title!=="string"||!Array.isArray(row.selectedOffers)))throw Error("market_products_invalid");
 return value;
}
function readCatalog(market:string){
 return run("market-catalog-status.py",["status","--market",market],market,validateCatalog);
}
function readModel(market:string){
 return run("project-market-read-model.py",["read","--market",market,"--view","catalog","--max-age","300"],market,validateCatalog,10_000);
}
function project(market:string){
 const root=projectRoot();
 return new Promise<void>((resolve,reject)=>execFile(join(root,".venv/bin/python"),
  [join(root,"scripts/project-market-read-model.py"),"project","--market",market,"--view","catalog"],
  {cwd:root,timeout:60_000,maxBuffer:1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"}},
  error=>error?reject(Error("market_catalog_projection_failed")):resolve()));
}
export function readProducts(market:string,offset:number,query:string,scope:MarketProductsState["scope"]="current"){
 return run("market-catalog-status.py",["products","--market",market,"--offset",String(offset),"--query",query,"--snapshot",scope],market,(raw,selected)=>validateProducts(raw,selected,scope),20_000);
}
async function get(request:Request){
 if(!isLocalRequest(request,false))return Response.json({error:"local_origin_required"},{status:403,headers});
 const url=new URL(request.url),params=url.searchParams;
 let market:string;
 try{
  if(params.getAll("market").length!==1||[...params.keys()].some(key=>!["market","view","offset","q","snapshot"].includes(key)))throw Error();
  market=cleanMarket(params.get("market"));
 }catch{return Response.json({error:"invalid_query"},{status:400,headers});}
 const view=params.get("view");
 if(view!==null&&view!=="products")return Response.json({error:"invalid_query"},{status:400,headers});
 if(view==="products"){
  if(params.getAll("view").length!==1||params.getAll("offset").length>1||params.getAll("q").length>1||params.getAll("snapshot").length>1)return Response.json({error:"invalid_query"},{status:400,headers});
  const scope=params.get("snapshot")??"current";
  if(scope!=="current"&&scope!=="category"&&scope!=="weekly")return Response.json({error:"invalid_query"},{status:400,headers});
  const definition=operationalMarket(market)!;
  if(definition.capabilities.fullManagedCatalog!==true)return Response.json({market,scope,availability:definition.capabilities.fullManagedCatalog===false?"unsupported":"unavailable",items:[],total:0,offset:0,limit:30,observedAt:null,readOnly:true,platformWrites:0},{headers});
  const rawOffset=params.get("offset")??"0",query=params.get("q")??"";
  if(!/^(0|[1-9][0-9]{0,6})$/.test(rawOffset)||Number(rawOffset)>1_000_000||query.length>100)return Response.json({error:"invalid_query"},{status:400,headers});
  try{return Response.json(await readProducts(market,Number(rawOffset),query,scope),{headers});}
  catch{return Response.json({error:"market_products_unavailable"},{status:503,headers});}
 }
 if(params.has("offset")||params.has("q")||params.has("snapshot"))return Response.json({error:"invalid_query"},{status:400,headers});
 try{return Response.json(await singleflight(`catalog:${market}`,()=>readModel(market).catch(()=>readCatalog(market))),{headers});}
 catch{return Response.json({error:"market_catalog_unavailable"},{status:503,headers});}
}
async function post(request:Request){
 if(!isLocalRequest(request,true))return Response.json({error:"local_origin_required"},{status:403,headers});
 let market:string,urlMarket:string;
 try{
  const url=new URL(request.url);
  if([...url.searchParams.keys()].some(key=>key!=="market")||url.searchParams.getAll("market").length!==1)throw Error();
  urlMarket=cleanMarket(url.searchParams.get("market"));
  if(request.headers.get("content-type")?.split(";")[0].trim()!=="application/json")return Response.json({error:"json_required"},{status:415,headers});
  const raw=await request.text();if(new TextEncoder().encode(raw).length>1024)throw Error();
  const value=JSON.parse(raw);
  if(!value||typeof value!=="object"||Array.isArray(value)||Object.keys(value).sort().join(",")!=="action,market"||value.action!=="preview_campaign")throw Error();
  market=cleanMarket(value.market);
  if(market!==urlMarket)return Response.json({error:"market_mismatch"},{status:409,headers});
 }catch{return Response.json({error:"invalid_market_catalog_request"},{status:400,headers});}
 try{
  const result=await run("market-catalog-status.py",["preview-campaign","--market",market!],market!,validateCatalog);
  await project(market!);return Response.json(result,{headers});
 }catch{return Response.json({error:"market_catalog_unavailable"},{status:503,headers});}
}
export const handlers={GET:get,POST:post};

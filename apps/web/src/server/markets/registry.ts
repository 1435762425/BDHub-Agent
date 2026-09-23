import {readFileSync,statSync} from "node:fs";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import type {MarketRegistry,MarketSummary} from "../../features/shell/market-types";

const KEY=/^[a-z]{2}$/;
const ACCOUNT=/^acc[1-9][0-9]*$/;
let cache:{mtimeMs:number;value:MarketRegistry}|null=null;
export function readMarketRegistry():MarketRegistry{
 const path=join(projectRoot(),"config/markets.json"),mtimeMs=statSync(path).mtimeMs;
 if(cache?.mtimeMs===mtimeMs)return cache.value;
 const raw=JSON.parse(readFileSync(path,"utf8")) as Record<string,unknown>;
 if(raw.schemaVersion!==2||typeof raw.defaultMarket!=="string"||!raw.markets||typeof raw.markets!=="object"||Array.isArray(raw.markets))throw Error("market_registry_invalid");
 const markets:MarketSummary[]=[];
 for(const [key,value] of Object.entries(raw.markets as Record<string,unknown>)){
  if(!KEY.test(key)||!value||typeof value!=="object"||Array.isArray(value))throw Error("market_registry_invalid");
  const row=value as Record<string,unknown>,cap=row.capabilities as Record<string,unknown>,accounts=row.accounts as Record<string,unknown>;
  if(row.enabled!==true)continue;
  if(!["ready","paused","planned"].includes(String(row.runtimeState))||typeof row.contentReady!=="boolean"||!cap||cap.campaignCatalog!==true||!(typeof cap.fullManagedCatalog==="boolean"||cap.fullManagedCatalog===null)||!accounts||!Object.prototype.hasOwnProperty.call(accounts,"communications")||!Object.prototype.hasOwnProperty.call(accounts,"supply"))throw Error("market_registry_invalid");
  const text=(name:string)=>{const current=row[name];if(typeof current!=="string"||!current||current.length>80)throw Error("market_registry_invalid");return current;};
  const nullableText=(name:string)=>row[name]===null?null:text(name),account=(name:string)=>accounts[name]===null?null:typeof accounts[name]==="string"&&ACCOUNT.test(accounts[name] as string)?accounts[name] as string:(()=>{throw Error("market_registry_invalid")})();
  const communications=account("communications"),supply=account("supply"),operational=row.runtimeState!=="planned";
  if(operational&&(!communications||!supply||communications===supply||cap.fullManagedCatalog===null||!row.contentReady))throw Error("market_registry_invalid");
  if(!operational&&(communications!==null||supply!==null))throw Error("market_registry_invalid");
  const locale=nullableText("locale"),currency=nullableText("currency"),timeZone=nullableText("timeZone"),templateLanguage=nullableText("templateLanguage");
  if(row.contentReady&&(!locale||!templateLanguage))throw Error("market_registry_invalid");
  markets.push({key,label:text("label"),shortLabel:text("shortLabel"),runtimeState:row.runtimeState as MarketSummary["runtimeState"],contentReady:row.contentReady,locale,currency,timeZone,platformRegion:text("platformRegion"),templateLanguage,accounts:{communications,supply},capabilities:{campaignCatalog:true,fullManagedCatalog:cap.fullManagedCatalog as boolean|null}});
 }
 if(!markets.some(row=>row.key===raw.defaultMarket))throw Error("market_registry_invalid");
 const result={defaultMarket:raw.defaultMarket,markets};cache={mtimeMs,value:result};return result;
}
export function enabledMarket(key:string){return readMarketRegistry().markets.find(row=>row.key===key)??null;}
export function operationalMarket(key:string){const row=enabledMarket(key);return row&&row.runtimeState!=="planned"?row:null;}

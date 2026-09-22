import {readFileSync} from "node:fs";
import {join} from "node:path";
import {projectRoot} from "../creator-identities/refresh.ts";
import type {MarketRegistry,MarketSummary} from "../../features/shell/market-types";

const KEY=/^[a-z]{2}$/;
export function readMarketRegistry():MarketRegistry{
 const raw=JSON.parse(readFileSync(join(projectRoot(),"config/markets.json"),"utf8")) as Record<string,unknown>;
 if(raw.schemaVersion!==1||typeof raw.defaultMarket!=="string"||!raw.markets||typeof raw.markets!=="object"||Array.isArray(raw.markets))throw Error("market_registry_invalid");
 const markets:MarketSummary[]=[];
 for(const [key,value] of Object.entries(raw.markets as Record<string,unknown>)){
  if(!KEY.test(key)||!value||typeof value!=="object"||Array.isArray(value))throw Error("market_registry_invalid");
  const row=value as Record<string,unknown>,cap=row.capabilities as Record<string,unknown>;
  if(row.enabled!==true)continue;
  if(!cap||typeof cap.campaignCatalog!=="boolean"||typeof cap.fullManagedCatalog!=="boolean")throw Error("market_registry_invalid");
  const text=(name:string)=>{const current=row[name];if(typeof current!=="string"||!current||current.length>80)throw Error("market_registry_invalid");return current;};
  markets.push({key,label:text("label"),shortLabel:text("shortLabel"),locale:text("locale"),currency:text("currency"),timeZone:text("timeZone"),platformRegion:text("platformRegion"),templateLanguage:text("templateLanguage"),capabilities:{campaignCatalog:cap.campaignCatalog,fullManagedCatalog:cap.fullManagedCatalog}});
 }
 if(!markets.some(row=>row.key===raw.defaultMarket))throw Error("market_registry_invalid");
 return {defaultMarket:raw.defaultMarket,markets};
}
export function enabledMarket(key:string){return readMarketRegistry().markets.find(row=>row.key===key)??null;}

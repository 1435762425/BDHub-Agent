"use client";
import {useCallback,useEffect,useState} from "react";
import type {CampaignLinkState} from "../../server/campaign/bridge";

/** 非全托链接准备的覆盖情况；与其它卡片一致：一次读取、失败不把已有数字清空。 */
export function useCampaignLinks(market:string):{data:CampaignLinkState|null;loaded:boolean;reload:()=>Promise<void>}{
 const [data,setData]=useState<CampaignLinkState|null>(null);
 const [loaded,setLoaded]=useState(false);
 const reload=useCallback(async()=>{
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch(`/api/campaign-links?market=${encodeURIComponent(market)}`,{cache:"no-store"}).catch(()=>null);
   if(r?.ok){setData(await r.json());setLoaded(true);return;}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  throw Error('campaign_links_unavailable');
 },[market]);
 useEffect(()=>{void reload().catch(()=>{});},[reload]);
 return {data,loaded,reload};
}

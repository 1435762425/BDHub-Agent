"use client";
import {useCallback,useEffect,useState} from "react";
import type {CampaignPanelState} from "../../server/campaign/bridge";

/** 非全托商品池的摘要；与全托那几个 hook 一样：一次读取、失败不把数字清空。 */
export function useCampaignPanel(market:string):{data:CampaignPanelState|null;loaded:boolean;reload:()=>Promise<void>}{
 const [data,setData]=useState<CampaignPanelState|null>(null);
 const [loaded,setLoaded]=useState(false);
 const reload=useCallback(async()=>{
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch(`/api/campaign-panel?market=${encodeURIComponent(market)}`,{cache:"no-store"}).catch(()=>null);
   if(r?.ok){setData(await r.json());setLoaded(true);return;}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  throw Error('campaign_panel_unavailable');
 },[market]);
 useEffect(()=>{void reload().catch(()=>{});},[reload]);
 return {data,loaded,reload};
}

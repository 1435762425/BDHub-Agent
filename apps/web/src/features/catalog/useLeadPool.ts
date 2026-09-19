"use client";
import {useCallback,useEffect,useState} from "react";
import type {LeadPoolState} from "../../server/lead-pool/bridge";

/** One owner for the pool fetch: the funnel row and the pool card read the same numbers. */
export function useLeadPool():{data:LeadPoolState|null;reload:()=>Promise<void>;loaded:boolean}{
 const [data,setData]=useState<LeadPoolState|null>(null);
 // A first read that has not landed yet is loading, not unavailable.
 const [loaded,setLoaded]=useState(false);
 const reload=useCallback(async()=>{
  // The pool is computed while another worker writes the same database, so one refused read is
  // noise. Retry, then keep whatever was last shown rather than replacing numbers with "unavailable".
  for(let attempt=0;attempt<3;attempt+=1){
   const r=await fetch("/api/lead-pool",{cache:"no-store"}).catch(()=>null);
   if(r?.ok){setData(await r.json());setLoaded(true);return;}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  throw Error('lead_pool_unavailable');
 },[]);
 useEffect(()=>{void reload().catch(()=>{});},[reload]);
 return {data,reload,loaded};
}

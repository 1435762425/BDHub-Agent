"use client";
import {useCallback,useEffect,useState} from "react";
import type {CampaignJoinState} from "../../server/campaign/bridge";

/** 加入活动的状态与三个动作。勾选只允许"当前预览里可加入"的活动，避免拿旧 id 去提交。 */
export type CampaignJoinController={data:CampaignJoinState|null;loaded:boolean;busy:boolean;
 message:string|null;email:string;setEmail:(value:string)=>void;
 reload:()=>Promise<void>;preview:()=>Promise<void>;recheck:()=>Promise<void>;joinAll:()=>Promise<void>};

export function useCampaignJoin(market:string):CampaignJoinController{
 const [data,setData]=useState<CampaignJoinState|null>(null);
 const [loaded,setLoaded]=useState(false);
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState<string|null>(null);
 const [email,setEmail]=useState("");
 const reload=useCallback(async()=>{
  const r=await fetch(`/api/campaign-join?market=${encodeURIComponent(market)}`,{cache:"no-store"}).catch(()=>null);
  if(r?.ok){
   const value:CampaignJoinState=await r.json();
   setData(value);setLoaded(true);
   // 记住上次用过的邮箱："一键加入"第二次起才真的只需要一次点击。
   if(value.email)setEmail(current=>current||value.email!);
  }
 },[market]);
 useEffect(()=>{void reload();},[reload]);
 const post=useCallback(async(body:unknown,fail:string)=>{
  setBusy(true);setMessage(null);
  try{
   const r=await fetch(`/api/campaign-join?market=${encodeURIComponent(market)}`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({...body as Record<string,unknown>,market})});
   const value=await r.json();
   if(!r.ok){setMessage(value?.error==="campaign_write_requires_verification"
    ?"有活动提过但没结算：先点「回查未结算」，不会重复提交。":`${fail}（${value?.error??r.status}）`);return;}
   setData(value);setLoaded(true);
   return value as CampaignJoinState;
  }catch{setMessage(fail);return undefined;}
  finally{setBusy(false);}
 },[market]);
 const preview=useCallback(async()=>{
  const value=await post({action:"preview"},"暂时无法预览可加入活动。");
  if(value?.available){
   setMessage(`预览完成：可加入 ${value.eligible??0} 个，已加入 ${value.joined??0} 个。`);
  }
 },[post]);
 const recheck=useCallback(async()=>{
  const value=await post({action:"verify"},"暂时无法回查。");
  if(value?.available)setMessage(`回查完成：结算了 ${value.settled??0} 条${value.unresolved?.length?`，仍有 ${value.unresolved.length} 个待核验`:"，没有待核验的。"}已加入 ${value.joinedCount??0} 个。`);
 },[post]);
 // 一键加入：服务端会**先重新预览一遍**再提交当前全部合格的活动，所以页面不传活动名单，
 // 也不依赖页面上这份可能已经过期的列表。
 const joinAll=useCallback(async()=>{
  if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)){setMessage("请先填一个有效的联系邮箱——平台加入活动时要求填写，填一次会记住。");return;}
  const value=await post({action:"joinAll",email,confirm:true},"暂时无法提交加入。");
  if(value?.available){
   const joined=value.appliedCounts?.joined??0;
   const skipped=value.appliedCounts?.skipped??0;
   const unknown=(value.appliedCounts?.result_unknown??0)+(value.appliedCounts?.writing??0);
   setMessage(value.state==="needs_verification"
    ?`提交完成：已加入 ${joined} 个，跳过 ${skipped} 个，还有 ${unknown} 个结果未知——先点「回查未结算」确认，不会重新提交。`
    :`提交完成：这一批可加入 ${value.eligible??0} 个，已加入 ${joined} 个，跳过 ${skipped} 个。`);
  }
 },[post,email]);
 return {data,loaded,busy,message,email,setEmail,reload,preview,recheck,joinAll};
}

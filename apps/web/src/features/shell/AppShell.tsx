"use client";

import Link from "next/link";
import {usePathname} from "next/navigation";
import {useEffect,useState,type ReactNode} from "react";
import {ThemeToggleButton} from "@/components/common/ThemeToggleButton";
import {Button,Icon,type IconName} from "@/features/bdhub/ui";
import type {MarketSummary} from "./market-types";
import OpsAlertBar from "./OpsAlertBar";

export default function AppShell({children,currentMarket,markets}:{children:ReactNode;currentMarket:MarketSummary;markets:MarketSummary[]}){
 const pathname=usePathname();
 const base=`/${currentMarket.key}`;
 const navigation:{href:string;label:string;icon:IconName;match:string}[]=[
  {href:`${base}/console`,label:"经营总览",icon:"chart",match:`${base}/console`},
  {href:base,label:"市场运营",icon:"bolt",match:base},
  {href:`${base}/workspace/send`,label:"合作工作台",icon:"chat",match:`${base}/workspace`},
  {href:`${base}/conversations`,label:"会话",icon:"bell",match:`${base}/conversations`},
  {href:`${base}/catalog`,label:"货盘",icon:"grid",match:`${base}/catalog`},
  {href:`${base}/creators`,label:"达人",icon:"users",match:`${base}/creators`},
  {href:`${base}/ops/jobs`,label:"运行与设置",icon:"settings",match:`${base}/ops`},
 ];
 const conversationWide=pathname.startsWith(`${base}/conversations`);
 const connectedOrder=new Map(["br","my","de","fr","it","uk","th"].map((key,index)=>[key,index]));
 const connectedMarkets=markets.filter(row=>row.runtimeState!=="planned").sort((a,b)=>(connectedOrder.get(a.key)??100)-(connectedOrder.get(b.key)??100));
 const currentPlanned=currentMarket.runtimeState==="planned"?currentMarket:null;
 const allMarkets=pathname.endsWith("/console");
 const [collapsed,setCollapsed]=useState(false),[mobile,setMobile]=useState(false);
 useEffect(()=>{try{setCollapsed(localStorage.getItem("bdhub-agent-sidebar")==="collapsed");}catch{}},[]);
 useEffect(()=>setMobile(false),[pathname]);
 const toggle=()=>{
  if(window.innerWidth<1024){setMobile(value=>!value);return;}
  setCollapsed(value=>{const next=!value;try{localStorage.setItem("bdhub-agent-sidebar",next?"collapsed":"expanded");}catch{}return next;});
 };
 return <div id="bdhub-shell" className="min-h-screen">
  {mobile&&<button aria-label="关闭移动导航" onClick={()=>setMobile(false)} className="fixed inset-0 z-40 bg-gray-900/40 lg:hidden"/>}
  <aside className={`fixed inset-y-0 left-0 z-50 flex w-[232px] flex-col border-r border-gray-200 bg-white px-4 transition-[width,transform] duration-200 dark:border-gray-800 dark:bg-gray-900 ${mobile?"visible translate-x-0":"invisible -translate-x-full lg:visible lg:translate-x-0"} ${collapsed?"lg:w-[76px]":"lg:w-[232px]"}`}>
   <Link href={base} aria-label="BDHub Agent 首页" className={`flex h-16 items-center gap-2.5 px-2 ${collapsed?"lg:justify-center":""}`}>
    <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-brand-500 text-white"><Icon name="grid" className="size-6"/></span>
    <span className={collapsed?"lg:hidden":""}><span className="block text-lg font-semibold tracking-tight text-gray-900 dark:text-white">BDHub <span className="font-normal text-gray-500">Agent</span></span></span>
   </Link>
   <div className={`mx-1 mt-4 ${collapsed?"lg:hidden":""}`}>
    <label htmlFor="market-switch" className="sr-only">当前市场</label>
    <select id="market-switch" value={currentMarket.key} onChange={event=>{const target=event.target.value;window.location.assign(pathname.replace(new RegExp(`^/${currentMarket.key}(?=/|$)`),`/${target}`));}} className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm font-medium text-gray-700 outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
     <optgroup label="正式接入">{connectedMarkets.map(row=><option key={row.key} value={row.key}>{row.label} · {row.shortLabel}</option>)}</optgroup>
     {currentPlanned&&<optgroup label="待接入"><option value={currentPlanned.key}>{currentPlanned.label} · {currentPlanned.shortLabel}</option></optgroup>}
    </select>
   </div>
   <nav aria-label="主导航" className="mt-5 flex-1 space-y-1 overflow-y-auto">
    {navigation.map(item=>{const active=item.href===base?pathname===base:pathname.startsWith(item.match);return <Link key={item.href} href={item.href} title={item.label} aria-current={active?"page":undefined} className={`menu-item ${active?"menu-item-active":"menu-item-inactive"} ${collapsed?"lg:justify-center lg:px-2":""}`}><Icon name={item.icon} className="size-6 shrink-0"/><span className={collapsed?"lg:hidden":""}>{item.label}</span></Link>;})}
   </nav>
  </aside>
  <div className={`min-h-screen transition-[margin] duration-200 ${collapsed?"lg:ml-[76px]":"lg:ml-[232px]"}`}>
   <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-gray-200 bg-white/95 px-4 backdrop-blur-sm dark:border-gray-800 dark:bg-gray-900/95 sm:px-6">
    <div className="flex items-center gap-3"><Button variant="ghost" onClick={toggle} className="!size-10 !p-0" aria-label="切换导航"><Icon name="menu"/></Button><p className="text-sm font-medium text-gray-700 dark:text-gray-200">{allMarkets?"全部市场":`${currentMarket.label} · ${currentMarket.shortLabel}`}</p></div>
    <ThemeToggleButton/>
   </header>
   <OpsAlertBar/>
   <main className={conversationWide?"w-full p-3 sm:p-4 lg:p-5":"mx-auto max-w-[1680px] p-4 sm:p-6"}>{children}</main>
  </div>
 </div>;
}

"use client";
import {createContext,useCallback,useContext,useEffect,useReducer,useState,type ReactNode} from "react";
import {useRouter} from "next/navigation";
import {demoReducer,initialState,type DemoState,type DemoAction,type View} from "./model";
const KEY="bdhub-agent-tailadmin-prototype-v1";
interface DemoContextType {state:DemoState;dispatch:React.Dispatch<DemoAction>;notify:(message:string)=>void;go:(view:View,params?:Record<string,string>)=>void;openCase:(id:string)=>void;hydrated:boolean;}
const DemoContext=createContext<DemoContextType|null>(null);
export function DemoProvider({children}:{children:ReactNode}){
 const [state,dispatch]=useReducer(demoReducer,undefined,initialState);const [hydrated,setHydrated]=useState(false);const [toast,setToast]=useState("");const router=useRouter();
 useEffect(()=>{try{const raw=localStorage.getItem(KEY);if(raw){const parsed=JSON.parse(raw);if(parsed.version===1&&Array.isArray(parsed.cases)&&Array.isArray(parsed.creators)&&Array.isArray(parsed.relations)&&parsed.agent)dispatch({type:"hydrate",state:parsed});}}catch{}setHydrated(true);},[]);
 useEffect(()=>{if(hydrated)try{localStorage.setItem(KEY,JSON.stringify(state));}catch{setToast("浏览器存储不可用，本轮变化仅保留在当前页面。");}},[state,hydrated]);
 useEffect(()=>{if(!toast)return;const id=setTimeout(()=>setToast(""),4200);return()=>clearTimeout(id);},[toast]);
 const notify=useCallback((message:string)=>setToast(message),[]);
 const go=useCallback((view:View,params?:Record<string,string>)=>{router.push(`/${view}${params?`?${new URLSearchParams(params)}`:""}`);},[router]);
 const openCase=useCallback((id:string)=>{const item=state.cases.find(x=>x.id===id);const creator=state.creators.find(x=>x.id===item?.creatorId);if(creator&&state.marketFilter!=="all"&&state.marketFilter!==creator.market)dispatch({type:"market",market:creator.market});go("workspace",{case:id});},[state.cases,state.creators,state.marketFilter,go]);
 return <DemoContext.Provider value={{state,dispatch,notify,go,openCase,hydrated}}>{children}{toast&&<div role="status" className="fixed bottom-6 left-1/2 z-[100000] max-w-[90vw] -translate-x-1/2 rounded-xl bg-gray-900 px-5 py-3 text-sm text-white shadow-theme-lg dark:bg-gray-700">{toast}</div>}</DemoContext.Provider>;
}
export function useDemo(){const value=useContext(DemoContext);if(!value)throw new Error("DemoProvider missing");return value;}

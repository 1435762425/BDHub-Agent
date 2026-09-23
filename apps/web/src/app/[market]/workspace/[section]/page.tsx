import {notFound,redirect} from "next/navigation";
import SecondOutreachWorkspace from "@/features/second-outreach/SecondOutreachWorkspace";
import {enabledMarket} from "@/server/markets/registry";

const sections=["send","history"] as const;
export default async function Page({params}:{params:Promise<{market:string;section:string}>}){
 const {market,section}=await params;const current=enabledMarket(market);if(!current)notFound();
 if(section==="inbox")redirect(`/${market}/conversations`);
 if(!sections.includes(section as (typeof sections)[number]))notFound();
 return <SecondOutreachWorkspace market={market} label={current.label} runtimeAvailable={current.runtimeState!=="planned"&&current.contentReady} section={section as (typeof sections)[number]}/>;
}

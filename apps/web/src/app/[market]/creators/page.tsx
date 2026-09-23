import {notFound} from "next/navigation";
import CreatorIdentityWorkspace from "@/features/creator-identities/CreatorIdentityWorkspace";
import {enabledMarket} from "@/server/markets/registry";
export default async function Page({params}:{params:Promise<{market:string}>}){const {market}=await params,current=enabledMarket(market);if(!current)notFound();return <CreatorIdentityWorkspace market={market} marketLabel={current.label} identityWriteAvailable={market==="it"&&current.runtimeState==="ready"}/>;}

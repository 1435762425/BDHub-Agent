import {notFound} from "next/navigation";
import CreatorIdentityWorkspace from "@/features/creator-identities/CreatorIdentityWorkspace";
import type {IdentityMarket} from "@/features/creator-identities/contracts";
import {enabledMarket} from "@/server/markets/registry";
export default async function Page({params}:{params:Promise<{market:string}>}){const {market}=await params;if(!enabledMarket(market))notFound();return <CreatorIdentityWorkspace market={market as IdentityMarket}/>;}

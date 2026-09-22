import {notFound} from "next/navigation";import ConversationWorkspace from "@/features/conversations/ConversationWorkspace";import {enabledMarket} from "@/server/markets/registry";
export default async function Page({params}:{params:Promise<{market:string}>}){const {market}=await params;if(!enabledMarket(market))notFound();return <ConversationWorkspace market={market} section="conversations"/>;}

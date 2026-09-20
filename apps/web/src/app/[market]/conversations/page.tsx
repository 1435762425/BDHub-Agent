import {notFound} from "next/navigation";import ConversationWorkspace from "@/features/conversations/ConversationWorkspace";
export default async function Page({params}:{params:Promise<{market:string}>}){const {market}=await params;if(market!=="it")notFound();return <ConversationWorkspace section="conversations"/>;}

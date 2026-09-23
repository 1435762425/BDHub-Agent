import {notFound} from "next/navigation";
import CatalogWorkspace from "@/features/catalog/CatalogWorkspace";
import {enabledMarket} from "@/server/markets/registry";
export default async function Page({params}:{params:Promise<{market:string}>}){const {market}=await params,definition=enabledMarket(market);if(!definition)notFound();return <CatalogWorkspace definition={definition}/>;}

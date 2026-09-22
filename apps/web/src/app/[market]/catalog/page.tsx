import {notFound} from "next/navigation";
import CatalogWorkspace from "@/features/catalog/CatalogWorkspace";
import MarketCatalogWorkspace from "@/features/catalog/MarketCatalogWorkspace";
import {enabledMarket} from "@/server/markets/registry";
export default async function Page({params}:{params:Promise<{market:string}>}){const {market}=await params;if(!enabledMarket(market))notFound();return market==="it"?<CatalogWorkspace/>:<MarketCatalogWorkspace market={market}/>;}

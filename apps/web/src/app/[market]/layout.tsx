import {notFound} from "next/navigation";
import AppShell from "@/features/shell/AppShell";
import {readMarketRegistry} from "@/server/markets/registry";

export default async function MarketLayout({children,params}:{children:React.ReactNode;params:Promise<{market:string}>}){
 const {market}=await params;
 const registry=readMarketRegistry(),current=registry.markets.find(row=>row.key===market);
 if(!current)notFound();
 return <AppShell currentMarket={current} markets={registry.markets}>{children}</AppShell>;
}

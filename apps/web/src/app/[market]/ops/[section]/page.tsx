import {notFound,redirect} from "next/navigation";
import OpsWorkspace,{type OpsSection} from "@/features/ops/OpsWorkspace";
import {enabledMarket} from "@/server/markets/registry";

export default async function Page({params}:{params:Promise<{market:string;section:string}>}){
 const {market,section}=await params;const current=enabledMarket(market);
 if(!current)notFound();
 if(section==="reply-evaluation")redirect(`/${market}/conversations/agent#reply-evaluation`);
 if(!["jobs","kalodata","accounts"].includes(section))notFound();
 return <OpsWorkspace market={market} section={section as OpsSection} fullManagedCatalog={current.capabilities.fullManagedCatalog}/>;
}

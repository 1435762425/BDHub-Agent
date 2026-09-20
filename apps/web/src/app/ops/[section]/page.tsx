import {notFound,redirect} from "next/navigation";
import MarketAccountsPage from "@/features/accounts/MarketAccountsPage";
import OpsWorkspace from "@/features/ops/OpsWorkspace";

export default async function Page({params}:{params:Promise<{section:string}>}){
 const {section}=await params;
 if(section==="jobs"||section==="kalodata")return <OpsWorkspace section={section}/>;
 if(section==="accounts")return <MarketAccountsPage/>;
 if(section==="reply-evaluation")redirect("/it/conversations/agent#reply-evaluation");
 notFound();
}

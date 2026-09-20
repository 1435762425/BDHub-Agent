import {notFound} from "next/navigation";
import MarketAccountsPage from "@/features/accounts/MarketAccountsPage";
import OpsWorkspace from "@/features/ops/OpsWorkspace";
import ReplyReviewPanel from "@/features/second-outreach/ReplyReviewPanel";

export default async function Page({params}:{params:Promise<{section:string}>}){
 const {section}=await params;
 if(section==="jobs")return <OpsWorkspace/>;
 if(section==="accounts")return <MarketAccountsPage/>;
 if(section==="reply-evaluation")return <div className="space-y-5"><ReplyReviewPanel/></div>;
 notFound();
}

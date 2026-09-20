import {notFound,redirect} from "next/navigation";
import OpsWorkspace from "@/features/ops/OpsWorkspace";

export default async function Page({params}:{params:Promise<{section:string}>}){
 const {section}=await params;
 if(section==="jobs"||section==="kalodata"||section==="accounts")return <OpsWorkspace section={section}/>;
 if(section==="reply-evaluation")redirect("/it/conversations/agent#reply-evaluation");
 notFound();
}

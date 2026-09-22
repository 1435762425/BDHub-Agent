import {notFound,redirect} from "next/navigation";

export default async function Page({params}:{params:Promise<{section:string}>}){
 const {section}=await params;
 if(section==="jobs"||section==="kalodata"||section==="accounts")redirect(`/it/ops/${section}`);
 if(section==="reply-evaluation")redirect("/it/conversations/agent#reply-evaluation");
 notFound();
}

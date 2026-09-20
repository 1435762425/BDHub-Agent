import {notFound,redirect} from "next/navigation";
import SecondOutreachWorkspace from "@/features/second-outreach/SecondOutreachWorkspace";

const sections=["send","history"] as const;
export default async function Page({params}:{params:Promise<{section:string}>}){
 const {section}=await params;
 if(section==="inbox")redirect("/it/conversations");
 if(!sections.includes(section as (typeof sections)[number]))notFound();
 return <SecondOutreachWorkspace section={section as (typeof sections)[number]}/>;
}

import {notFound} from "next/navigation";
import SecondOutreachWorkspace from "@/features/second-outreach/SecondOutreachWorkspace";

const sections=["send","inbox","history"] as const;
export default async function Page({params}:{params:Promise<{section:string}>}){
 const {section}=await params;
 if(!sections.includes(section as (typeof sections)[number]))notFound();
 return <SecondOutreachWorkspace section={section as (typeof sections)[number]}/>;
}

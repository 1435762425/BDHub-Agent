import {notFound} from "next/navigation";
import FirstOutreachWorkspace from "@/features/first-outreach/FirstOutreachWorkspace";
export default async function FirstOutreachPage({params}:{params:Promise<{market:string}>}){
 const {market}=await params;if(market!=="br")notFound();
 return <FirstOutreachWorkspace/>;
}

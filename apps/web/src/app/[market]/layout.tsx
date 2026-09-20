import {notFound} from "next/navigation";
import AppShell from "@/features/shell/AppShell";

export default async function MarketLayout({children,params}:{children:React.ReactNode;params:Promise<{market:string}>}){
 const {market}=await params;
 if(market!=="it")notFound();
 return <AppShell>{children}</AppShell>;
}

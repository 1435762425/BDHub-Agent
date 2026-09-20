import {notFound} from "next/navigation";import OperationsHome from "@/features/operations/OperationsHome";
export default async function Page({params}:{params:Promise<{market:string}>}){const {market}=await params;if(market!=="it")notFound();return <OperationsHome/>;}

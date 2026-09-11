import {Suspense} from "react";
import {notFound} from "next/navigation";
import PrototypeApp from "@/features/bdhub/PrototypeApp";
import type {View} from "@/features/bdhub/model";
const views:View[]=["overview","goals","opportunities","workspace","results","agents","settings"];
export function generateStaticParams(){return views.map(view=>({view}));}
export default async function Page({params}:{params:Promise<{view:string}>}){const {view}=await params;if(!views.includes(view as View))notFound();return <Suspense fallback={<div className="p-10 text-gray-500">正在加载工作台…</div>}><PrototypeApp view={view as View}/></Suspense>;}

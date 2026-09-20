"use client";
import Link from "next/link";
import {PageHeading,Pill} from "../bdhub/ui";
import JobsPanel from "./JobsPanel";
import KalodataIdentityPanel from "./KalodataIdentityPanel";
import MarketAccountsPage from "../accounts/MarketAccountsPage";
export type OpsSection="kalodata"|"jobs"|"accounts";
const tabs=[{id:"kalodata",label:"Kalodata 抓取身份"},{id:"jobs",label:"作业与定时"},{id:"accounts",label:"机构账号设置"}] as const;
export default function OpsWorkspace({section}:{section:OpsSection}){return <div className="space-y-5"><PageHeading title="运行与设置" description="抓取身份、主链作业时间和 TikTok 账号维护分开管理。" action={<Pill tone="neutral">设置本身不授予平台写入</Pill>}/><nav className="flex gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1 dark:bg-gray-800">{tabs.map(item=><Link key={item.id} href={`/ops/${item.id}`} className={`whitespace-nowrap rounded-lg px-4 py-2.5 text-sm font-medium ${section===item.id?"bg-white shadow dark:bg-gray-700":"text-gray-500"}`}>{item.label}</Link>)}</nav>{section==="kalodata"?<KalodataIdentityPanel/>:section==="jobs"?<JobsPanel/>:<MarketAccountsPage embedded/>}</div>;}

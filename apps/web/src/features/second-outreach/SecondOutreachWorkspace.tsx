"use client";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import CycleSupplyPanel from "./CycleSupplyPanel";
import SecondOutreachHistory from "./SecondOutreachHistory";
import {PageHeading} from "../bdhub/ui";
export default function SecondOutreachWorkspace(){const params=useSearchParams();if(params.get("history")==="1")return <SecondOutreachHistory/>;return <div className="space-y-5"><PageHeading title="二发经营工作台" description="自动补充线索、批量发送和回复；只处理需要你介入的事项。"/><CycleSupplyPanel/><Link className="text-xs text-gray-400" href="/workspace?mode=second-live&history=1">查看早期试点历史</Link></div>;}

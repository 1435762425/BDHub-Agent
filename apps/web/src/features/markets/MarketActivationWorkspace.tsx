import Link from "next/link";
import {Card,Notice,PageHeading,Pill} from "../bdhub/ui";
import MarketContentPanel from "./MarketContentPanel";

export default function MarketActivationWorkspace({market,label,area}:{market:string;label:string;area:"conversations"|"outreach"}){
 const conversations=area==="conversations";
 return <div className="space-y-5"><PageHeading title={conversations?"会话":"合作工作台"} description={`${label}市场使用独立达人、会话与发送台账，不会展示或消费意大利数据。`} action={<Pill tone="warning">持续发送保持关闭</Pill>}/>
 <Notice tone="info">机构、HTTP、IM、商品卡和发送能力均按市场独立验收；已通过的能力由运营首页开关控制，未通过的能力继续保持关闭。</Notice>
 <Card title={conversations?"会话台账尚为空":"发送池尚未发布"}><div className="space-y-3 p-6 text-sm leading-6 text-gray-500"><p>{conversations?"完成首轮 IM 只读同步后，这里会显示真实达人消息、加橱窗通知和待回复事项。":"Campaign/全托货盘、TapLink、达人线索和 OECID 全部形成市场内 generation 后，才会发布该市场发送池。"}</p><div className="flex flex-wrap gap-4"><Link href={`/${market}/catalog`} className="font-medium text-brand-500">查看货盘</Link><Link href={`/${market}/creators`} className="font-medium text-brand-500">查看达人</Link><Link href={`/${market}/ops/accounts`} className="font-medium text-brand-500">查看账号能力</Link></div></div></Card>
 <MarketContentPanel market={market}/>
 </div>;
}

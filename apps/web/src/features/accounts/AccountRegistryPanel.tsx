"use client";

import {useCallback,useEffect,useState,type FormEvent} from "react";
import {Button,Card,Dialog,Field,Input,Notice,Pill} from "../bdhub/ui";
import type {Registry,RegistryRow} from "../../server/account-registry/bridge";

const roleLabels:Record<string,string>={communications:"通讯",supply:"供给"};
type Draft={account:string;market:string;partnerId:string;username:string;password:string};
const emptyDraft:Draft={account:"",market:"",partnerId:"",username:"",password:""};
const errorLabels:Record<string,string>={
  account_exists:"该账号名已登记。",account_missing:"该账号不在登记表中。",
  invalid_registry_request:"填写内容不符合格式：账号名如 acc12，市场为两位小写字母，partner_id 为数字，用户名和密码不能有首尾空格。",
  account_registry_unavailable:"暂时无法读写账号登记表。",
};

export default function AccountRegistryPanel(){
  const [data,setData]=useState<Registry|null>(null),[message,setMessage]=useState(""),[busy,setBusy]=useState(false);
  const [adding,setAdding]=useState(false),[editing,setEditing]=useState<RegistryRow|null>(null),[draft,setDraft]=useState<Draft>(emptyDraft);
  const load=useCallback(async()=>{try{const response=await fetch("/api/account-registry",{cache:"no-store"});if(!response.ok)throw Error();setData(await response.json());}catch{setMessage("暂时无法读取账号登记表。");}},[]);
  useEffect(()=>{void load();},[load]);
  const close=()=>{setAdding(false);setEditing(null);setDraft(emptyDraft);};
  const submit=async(event:FormEvent)=>{
    event.preventDefault();setBusy(true);
    const body=editing?{action:"update",account:editing.account,...(draft.username?{username:draft.username}:{}),...(draft.password?{password:draft.password}:{})}:{action:"add",...draft};
    try{
      const response=await fetch("/api/account-registry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const value=await response.json();if(!response.ok)throw Error(value?.error);
      setData(value);setMessage(editing?`${editing.account} 的登录信息已更新；下次重登时使用新信息。`:`${draft.account} 已登记。分配到市场后可在上方发起首次重登。`);close();
    }catch(error){setMessage(errorLabels[error instanceof Error?error.message:""]??"保存未完成。");}finally{setBusy(false);}
  };
  const set=(key:keyof Draft)=>(event:{target:{value:string}})=>setDraft(value=>({...value,[key]:event.target.value}));
  return <Card title="账号登记" subtitle="本项目自己保存的 TikTok 账号登录信息，只供本项目使用；密码只写入、不回显。分配到哪个市场和角色在市场账号配置中决定。" action={<Button size="sm" onClick={()=>{setDraft(emptyDraft);setAdding(true);}}>新增账号</Button>}>
    <div className="space-y-3 p-5">
      {message&&<Notice tone="info">{message}</Notice>}
      {!data?<p className="text-sm text-gray-500">读取中…</p>:<div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <thead className="text-xs text-gray-500"><tr><th className="py-2 pr-4 font-medium">账号</th><th className="py-2 pr-4 font-medium">登记市场</th><th className="py-2 pr-4 font-medium">当前分配</th><th className="py-2 pr-4 font-medium">登录信息</th><th className="py-2"/></tr></thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">{data.accounts.map(row=><tr key={row.account}>
          <td className="py-2.5 pr-4 font-medium">{row.account.toUpperCase()}</td>
          <td className="py-2.5 pr-4">{row.market?.toUpperCase()??"—"}</td>
          <td className="py-2.5 pr-4">{row.assignment?<Pill tone="success">{row.assignment.market.toUpperCase()} · {roleLabels[row.assignment.role]??row.assignment.role}</Pill>:<Pill tone="neutral">未分配</Pill>}</td>
          <td className="py-2.5 pr-4"><Pill tone={row.usernameSet&&row.passwordSet?"success":"warning"}>{row.usernameSet&&row.passwordSet?"已保存":"不完整"}</Pill></td>
          <td className="py-2.5 text-right"><Button size="sm" variant="outline" onClick={()=>{setDraft(emptyDraft);setEditing(row);}}>修改登录信息</Button></td>
        </tr>)}</tbody>
      </table></div>}
    </div>
    <Dialog open={adding||editing!==null} onClose={close} title={editing?`修改 ${editing.account.toUpperCase()} 的登录信息`:"新增账号"} description={editing?"只填写要改的项，留空的保持不变。":"登记后账号还不会被使用；分配到市场后才会进入重登与能力验证。"}>
      <form className="space-y-4" onSubmit={event=>void submit(event)}>
        {!editing&&<><Field label="账号名" hint="如 acc12，登记后不能修改"><Input required value={draft.account} onChange={set("account")} placeholder="acc12" autoComplete="off"/></Field>
          <Field label="市场" hint="两位小写字母，如 it、uk"><Input required value={draft.market} onChange={set("market")} placeholder="uk" autoComplete="off"/></Field>
          <Field label="partner_id"><Input required value={draft.partnerId} onChange={set("partnerId")} inputMode="numeric" autoComplete="off"/></Field></>}
        <Field label="登录用户名"><Input required={!editing} value={draft.username} onChange={set("username")} autoComplete="off"/></Field>
        <Field label="登录密码"><Input required={!editing} type="password" value={draft.password} onChange={set("password")} autoComplete="new-password"/></Field>
        <div className="flex justify-end gap-2"><Button variant="outline" onClick={close}>取消</Button><Button type="submit" disabled={busy||(editing!==null&&!draft.username&&!draft.password)}>保存</Button></div>
      </form>
    </Dialog>
  </Card>;
}

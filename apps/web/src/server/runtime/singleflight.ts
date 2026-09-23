/** Coalesce concurrent read requests without retaining a stale result after completion. */
const pending=new Map<string,Promise<unknown>>();

export function singleflight<T>(key:string,read:()=>Promise<T>):Promise<T>{
 const existing=pending.get(key);
 if(existing)return existing as Promise<T>;
 const result=Promise.resolve().then(read);
 pending.set(key,result);
 void result.then(()=>{if(pending.get(key)===result)pending.delete(key);},()=>{if(pending.get(key)===result)pending.delete(key);});
 return result;
}

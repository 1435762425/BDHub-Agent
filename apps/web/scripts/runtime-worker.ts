import { resolve } from "node:path";
import { LocalRuntime } from "../src/server/runtime/engine.ts";

const args=process.argv.slice(2);
let dbPath=resolve(process.env.BDHUB_AGENT_RUNTIME_DB || resolve(process.cwd(),"../../var/runtime.sqlite"));
let interval=1000;
let once=false;
for(let i=0;i<args.length;i++) {
  if(args[i]==="--db"&&args[i+1]) dbPath=resolve(args[++i]);
  else if(args[i]==="--interval"&&args[i+1]) interval=Number(args[++i]);
  else if(args[i]==="--once") once=true;
  else throw new Error(`未知 Worker 参数：${args[i]}`);
}
if(!Number.isInteger(interval)||interval<100||interval>60_000) throw new Error("--interval 必须在 100 至 60000 毫秒之间。");
const runtime=new LocalRuntime(dbPath);
const workerId=`local-worker-${process.pid}`;
let stopping=false;
let wake:(()=>void)|undefined;
const stop=()=>{stopping=true;wake?.();};
process.once("SIGINT",stop);process.once("SIGTERM",stop);
console.log(`[${workerId}] 本地模拟 Worker 已启动；数据库：${dbPath}`);
try {
  do {
    try {
      const worked=await runtime.tick(workerId);
      if(worked) console.log(`[${workerId}] 已完成一个持久任务步骤。`);
    } catch(error) {
      // Do not reset a leased job after an uncertain submission. Its expiry is
      // recovered by the engine using the persisted attempt and fence.
      console.error(`[${workerId}] 本轮失败，任务按租约保留恢复：`,error instanceof Error?error.message:String(error));
      if(once) process.exitCode=1;
    }
    if(once||stopping) break;
    await new Promise<void>(resolveWait=>{
      const timer=setTimeout(()=>{wake=undefined;resolveWait();},interval);
      wake=()=>{clearTimeout(timer);wake=undefined;resolveWait();};
    });
  } while(!stopping);
} finally {runtime.close();}

import {resolve,dirname} from "node:path";
import {fileURLToPath} from "node:url";
import {SecondPilotStore} from "../src/server/second-pilot/store.ts";
const args=process.argv.slice(2);if(args.some(a=>a!=="--once")||args.length>1)throw new Error("Only --once is supported; this worker has no live transport.");
const root=resolve(dirname(fileURLToPath(import.meta.url)),"../../..");
const store=new SecondPilotStore(resolve(root,"var/second-italy.sqlite")),workerId=`second-local-${process.pid}`;let stopped=false,wake:(()=>void)|undefined;
const stop=()=>{stopped=true;wake?.();};process.once("SIGINT",stop);process.once("SIGTERM",stop);
try{do{try{await store.tick(workerId);}catch(error){console.error("Local second pilot step failed",error instanceof Error?error.name:"unknown");if(args.includes("--once"))process.exitCode=1;}
  if(stopped||args.includes("--once"))break;
  await new Promise<void>(done=>{const timer=setTimeout(()=>{wake=undefined;done();},1000);wake=()=>{clearTimeout(timer);wake=undefined;done();};});
}while(!stopped);}finally{store.close();}

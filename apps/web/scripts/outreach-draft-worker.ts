import {spawn} from "node:child_process";
import {existsSync} from "node:fs";
import {fileURLToPath} from "node:url";
import {join,resolve} from "node:path";

const root=fileURLToPath(new URL("../../../",import.meta.url));
const python=resolve(root,"../01-BDSystem-V2/.venv/bin/python");
if(!existsSync(python)){
  process.stderr.write("草稿 Worker 所需的 Python 环境未就绪。\n");
  process.exitCode=1;
}else{
  const child=spawn(python,[join(root,"scripts/outreach-drafts.py"),"worker",...process.argv.slice(2)],{
    cwd:root,stdio:"inherit",shell:false,env:{...process.env,PYTHONDONTWRITEBYTECODE:"1"},
  });
  const forward=(signal:NodeJS.Signals)=>{if(child.exitCode===null)child.kill(signal);};
  process.on("SIGTERM",forward);process.on("SIGINT",forward);
  child.once("error",()=>{process.stderr.write("草稿 Worker 无法启动。\n");process.exitCode=1;});
  child.once("exit",(code,signal)=>{
    process.removeListener("SIGTERM",forward);process.removeListener("SIGINT",forward);
    process.exitCode=code??(signal?130:1);
  });
}

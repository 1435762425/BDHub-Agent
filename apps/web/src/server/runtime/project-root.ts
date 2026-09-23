import {existsSync} from "node:fs";
import {dirname,join,resolve} from "node:path";

export function findProjectRoot(start=process.cwd()):string|null{
 for(let dir=resolve(start);;dir=dirname(dir)){
  if(existsSync(join(dir,"scripts/lib/creator_identity.py"))&&existsSync(join(dir,"apps/web/package.json")))return dir;
  if(dirname(dir)===dir)return null;
 }
}

export function projectRoot(start=process.cwd()):string{
 const root=findProjectRoot(start);
 if(root===null)throw Error("project_root_unavailable");
 return root;
}

import {resolve} from "node:path";
import {existsSync} from "node:fs";
import {MatchingStore,MatchingError} from "./store.ts";

const local = globalThis as unknown as {bdhubMatchingDatasets?:Map<string,MatchingStore>};
export function getMatchingStore(dataset:"demo"|"italy"="demo") {
  local.bdhubMatchingDatasets??=new Map();
  if(!local.bdhubMatchingDatasets.has(dataset)) {
    const path=resolve(dataset==="italy"?"../../var/matching-italy.sqlite":process.env.BDHUB_AGENT_MATCHING_DB || "../../var/matching.sqlite");
    if(dataset==="italy"&&!existsSync(path))throw new MatchingError(409,"dataset_not_imported","意大利离线资料尚未导入，请先运行本地导入脚本。");
    const store=new MatchingStore(path,{seed:dataset==="demo"});
    if(dataset==="italy"&&store.stats().mode!=="imported-offline") {store.close();throw new MatchingError(409,"dataset_mode_mismatch","此库不是已登记的真实离线资料，未加载。");}
    local.bdhubMatchingDatasets.set(dataset,store);
  }
  return local.bdhubMatchingDatasets.get(dataset)!;
}

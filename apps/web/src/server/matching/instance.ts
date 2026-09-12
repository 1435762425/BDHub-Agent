import {resolve} from "node:path";
import {existsSync} from "node:fs";
import {MatchingStore,MatchingError} from "./store.ts";
import {syncRegistryProfiles} from "./identity-profile-sync.ts";

const local = globalThis as unknown as {bdhubMatchingDatasets?:Map<string,MatchingStore>};
export function getMatchingStore(dataset:"demo"|"italy"|"italy-profiles"="demo") {
  local.bdhubMatchingDatasets??=new Map();
  if(!local.bdhubMatchingDatasets.has(dataset)) {
    const path=resolve(dataset==="italy-profiles"?"../../var/matching-italy-profiles.sqlite":dataset==="italy"?"../../var/matching-italy.sqlite":process.env.BDHUB_AGENT_MATCHING_DB || "../../var/matching.sqlite");
    if(dataset!=="demo"&&!existsSync(path))throw new MatchingError(409,"dataset_not_imported","意大利离线资料尚未导入，请先运行对应的本地导入脚本。");
    const store=new MatchingStore(path,{seed:dataset==="demo"});
    if(dataset!=="demo"&&(store.stats().mode!=="imported-offline"||!store.stats().dataset.id.startsWith(dataset==="italy-profiles"?"italy-profiles-":"italy-pilot-"))) {store.close();throw new MatchingError(409,"dataset_mode_mismatch","此库不是指定的真实离线资料，未加载。");}
    local.bdhubMatchingDatasets.set(dataset,store);
  }
  const store=local.bdhubMatchingDatasets.get(dataset)!;
  if(dataset==="italy-profiles")syncRegistryProfiles(store,resolve(process.env.BDHUB_AGENT_IDENTITY_DB||"../../var/creator-identities.sqlite"));
  return store;
}

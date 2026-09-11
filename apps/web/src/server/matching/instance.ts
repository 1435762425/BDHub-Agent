import {resolve} from "node:path";
import {MatchingStore} from "./store.ts";

const local = globalThis as unknown as {bdhubMatchingStore?:MatchingStore};
export function getMatchingStore() {
  if(!local.bdhubMatchingStore) local.bdhubMatchingStore = new MatchingStore(resolve(process.env.BDHUB_AGENT_MATCHING_DB || "../../var/matching.sqlite"));
  return local.bdhubMatchingStore;
}

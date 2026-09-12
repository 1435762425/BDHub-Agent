import {resolve} from "node:path";
import {CreatorIdentityReadStore} from "./store.ts";

// Web runs from apps/web; every request owns and closes its read-only connection.
// A database imported after server startup becomes visible on the next request.
export function getCreatorIdentityStore(){
  return new CreatorIdentityReadStore(resolve(process.env.BDHUB_AGENT_IDENTITY_DB||"../../var/creator-identities.sqlite"));
}

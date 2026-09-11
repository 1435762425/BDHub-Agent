import {resolve} from "node:path";
import {LocalRuntime} from "./engine.ts";

// The web application and CLI open independent connections to the same local WAL database.
// No default path, connection or credential points at the old BDHub.
const runtimeGlobal = globalThis as unknown as {bdhubLocalRuntime?: LocalRuntime};
export function getLocalRuntime(): LocalRuntime {
  if (!runtimeGlobal.bdhubLocalRuntime) {
    runtimeGlobal.bdhubLocalRuntime = new LocalRuntime(resolve(process.env.BDHUB_AGENT_RUNTIME_DB || "../../var/runtime.sqlite"));
  }
  return runtimeGlobal.bdhubLocalRuntime;
}

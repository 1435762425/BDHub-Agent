import {buildDraftContext,normalizeDraftContextRequest} from "./context.ts";
import {SecondOutreachError} from "../second-outreach/bridge.ts";
import type {DraftContextRequest} from "./context.ts";
/** Packet namespaces choose a fixed trusted compiler; clients cannot supply facts. */
export async function compileOutreachContext(input:DraftContextRequest){
  const request=normalizeDraftContextRequest(input);
  if(!request.packetId.startsWith("second-packet-"))return buildDraftContext(request);
  throw new SecondOutreachError("second_uses_templates",409,"二发已改用固定模板，不再调用模型生成话术。");
}

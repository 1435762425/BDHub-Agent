import {createAgentRepliesHandlers} from "../../../server/agent-replies/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const {GET,POST}=createAgentRepliesHandlers();

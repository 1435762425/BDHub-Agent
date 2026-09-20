import {createConversationHandlers} from "../../../server/conversations/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const {GET,POST}=createConversationHandlers();

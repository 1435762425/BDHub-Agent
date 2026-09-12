import {createDraftHandlers} from "../../../server/outreach-drafts/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const handlers=createDraftHandlers();
export const GET=handlers.GET;
export const POST=handlers.POST;

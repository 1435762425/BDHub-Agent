import {createSecondLiveHandlers} from "../../../server/second-live/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const handlers=createSecondLiveHandlers();
export const GET=handlers.GET;
export const POST=handlers.POST;

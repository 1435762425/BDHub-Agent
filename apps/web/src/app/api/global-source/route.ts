import {createGlobalGet,createGlobalPost} from "../../../server/global-source/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const GET=createGlobalGet();

export const POST=createGlobalPost();

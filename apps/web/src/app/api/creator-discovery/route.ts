import {createDiscoveryHandlers} from "../../../server/creator-identities/discovery.ts";

export const runtime="nodejs";
export const dynamic="force-dynamic";
const handlers=createDiscoveryHandlers();
export const GET=handlers.GET;
export const POST=handlers.POST;

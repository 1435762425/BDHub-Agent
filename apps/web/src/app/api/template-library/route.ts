import {createTemplateLibraryHandlers} from "../../../server/template-library/bridge.ts";
export const runtime="nodejs";export const dynamic="force-dynamic";
const handlers=createTemplateLibraryHandlers();export const GET=handlers.GET;export const POST=handlers.POST;

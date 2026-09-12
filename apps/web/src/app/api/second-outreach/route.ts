import {createSecondOutreachHandlers} from "@/server/second-outreach/bridge";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const {GET,POST}=createSecondOutreachHandlers();

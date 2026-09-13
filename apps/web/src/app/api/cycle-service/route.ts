import {createServiceHandlers} from "../../../server/cycle-service/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const {GET,POST}=createServiceHandlers();

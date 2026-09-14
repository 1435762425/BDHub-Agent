import {createTaskHandlers} from "../../../server/batch-tasks/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const {GET,POST}=createTaskHandlers();

import {createReplyReviewHandlers} from "../../../server/reply-review/bridge.ts";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export const {GET,POST}=createReplyReviewHandlers();

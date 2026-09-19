import test from "node:test";
import assert from "node:assert/strict";
import {initialSecondOutreachTab,SECOND_OUTREACH_TABS} from "../src/features/second-outreach/workspace-tabs.ts";

test("reply review has a stable direct workspace tab",()=>{
 assert.equal(initialSecondOutreachTab("reply"),"reply");
 assert(SECOND_OUTREACH_TABS.includes("reply"));
});

test("unknown or missing tabs cannot select a hidden workspace state",()=>{
 assert.equal(initialSecondOutreachTab(null),"send");
 assert.equal(initialSecondOutreachTab("send&action=start"),"send");
 assert.equal(initialSecondOutreachTab("unknown"),"send");
});

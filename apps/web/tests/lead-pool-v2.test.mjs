import test from "node:test";
import assert from "node:assert/strict";
import {validateLeadPool} from "../src/server/lead-pool/bridge.ts";

const counts={leads:10,merged:9,unresolved:1,queued:2,positions:10,creators:7,handles:8,
 sent:2,unsent:8,ready:2,readyCreators:2,cooling:1,awaitingReply:1,excluded:1,
 creatorsWithRelationship:7};
const layers={ready:2,queued:2,cooling:1,awaiting_reply:1,excluded:1,product_inactive:1,sent:2};

test("lead pool v2 exposes only three business outcomes and keeps sent as history",()=>{
 const value=validateLeadPool({schema:"bdhub.lead-pool.v2",available:true,now:1,counts,
  cooldown:{unlocked:86400,locked:172800},layers,pools:{},
  business:{sendable:2,waiting:4,inactive:2,total:8},
  reasons:{queued:2,cooling:1,awaiting_reply:1,excluded:1,product_inactive:1},history:{sent:2,currentPositions:2}});
 assert.deepEqual(value.business,{sendable:2,waiting:4,inactive:2,total:8});
 assert.equal(value.business.total+value.history.sent,value.counts.positions);
});

test("sent cannot be hidden inside the current pool total",()=>{
 assert.throws(()=>validateLeadPool({schema:"bdhub.lead-pool.v2",available:true,counts,
  cooldown:{unlocked:86400,locked:172800},layers,pools:{},
  business:{sendable:2,waiting:4,inactive:2,total:10},reasons:{},history:{sent:2,currentPositions:2}}));
});

test("unknown internal layers are rejected",()=>{
 assert.throws(()=>validateLeadPool({schema:"bdhub.lead-pool.v2",available:true,counts,
  cooldown:{unlocked:86400,locked:172800},layers:{...layers,mystery:1},pools:{},
  business:{sendable:2,waiting:4,inactive:2,total:8},reasons:{},history:{sent:2,currentPositions:2}}));
});

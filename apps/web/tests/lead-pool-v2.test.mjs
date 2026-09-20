import test from "node:test";
import assert from "node:assert/strict";
import {validateLeadPool} from "../src/server/lead-pool/bridge.ts";

const counts={leads:10,merged:9,unresolved:1,queued:2,positions:10,creators:7,handles:8,
 sent:2,unsent:8,ready:2,readyCreators:2,cooling:1,awaitingReply:1,excluded:1,
 creatorsWithRelationship:7,aPositions:7,bPositions:3,videoUnresolved:0};
const layers={ready:2,queued:2,cooling:1,awaiting_reply:1,excluded:1,product_inactive:1,sent:2};

test("lead pool v3 exposes A/B evidence, three business outcomes and sent history",()=>{
 const value=validateLeadPool({schema:"bdhub.lead-pool.v3",available:true,now:1,counts,
  cooldown:{unlocked:86400,locked:172800},layers,pools:{},
  business:{sendable:2,waiting:4,inactive:2,total:8},
  reasons:{queued:2,cooling:1,awaiting_reply:1,excluded:1,product_inactive:1},history:{sent:2,currentPositions:2}});
 assert.deepEqual(value.business,{sendable:2,waiting:4,inactive:2,total:8});
 assert.equal(value.business.total+value.history.sent,value.counts.positions);
});

test("sent cannot be hidden inside the current pool total",()=>{
 assert.throws(()=>validateLeadPool({schema:"bdhub.lead-pool.v3",available:true,counts,
  cooldown:{unlocked:86400,locked:172800},layers,pools:{},
  business:{sendable:2,waiting:4,inactive:2,total:10},reasons:{},history:{sent:2,currentPositions:2}}));
});

test("unknown internal layers are rejected",()=>{
 assert.throws(()=>validateLeadPool({schema:"bdhub.lead-pool.v3",available:true,counts,
  cooldown:{unlocked:86400,locked:172800},layers:{...layers,mystery:1},pools:{},
  business:{sendable:2,waiting:4,inactive:2,total:8},reasons:{},history:{sent:2,currentPositions:2}}));
});

test("A and B position evidence survives the Web decoder",()=>{
 const a={creatorId:"creator-a",handle:"seller.a",pid:"1".repeat(19),rank:1,units:12,sourceClass:"A",gmv:"450.5",videoViews:8000,videoId:"video-a",videoReleasedAt:"2026-09-18",unlocked:false,sentAt:null,readyAt:null,layer:"ready",caseUpdatedAt:null};
 const b={creatorId:"creator-b",handle:"seller.b",pid:"2".repeat(19),rank:null,units:0,sourceClass:"B",gmv:null,videoViews:12000,videoId:"video-b",videoReleasedAt:"2026-09-19",unlocked:false,sentAt:null,readyAt:null,layer:"ready",caseUpdatedAt:null};
 const value=validateLeadPool({schema:"bdhub.lead-pool.v3",available:true,now:1,counts,
  cooldown:{unlocked:86400,locked:172800},layers,pools:{ready:[a,b]},
  business:{sendable:2,waiting:4,inactive:2,total:8},reasons:{queued:2,cooling:1,awaiting_reply:1,excluded:1,product_inactive:1},history:{sent:2,currentPositions:2}});
 assert.equal(value.pools.ready[0].gmv,"450.5");
 assert.equal(value.pools.ready[1].sourceClass,"B");
 assert.equal(value.pools.ready[1].videoViews,12000);
});

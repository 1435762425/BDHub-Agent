import test from "node:test";
import assert from "node:assert/strict";
import {singleflight} from "../src/server/runtime/singleflight.ts";

test("same read shares one running process but starts fresh after completion or failure",async()=>{
 let started=0,release;
 const first=singleflight("singleflight-test:it",()=>{started++;return new Promise(resolve=>{release=resolve;});});
 const second=singleflight("singleflight-test:it",()=>{started++;return Promise.resolve(2);});
 await Promise.resolve();
 assert.equal(started,1);
 release(1);
 assert.deepEqual(await Promise.all([first,second]),[1,1]);
 assert.equal(await singleflight("singleflight-test:it",async()=>{started++;return 3;}),3);
 assert.equal(started,2);
 await assert.rejects(singleflight("singleflight-test:br",async()=>{throw Error("offline");}),/offline/);
 assert.equal(await singleflight("singleflight-test:br",async()=>4),4);
});

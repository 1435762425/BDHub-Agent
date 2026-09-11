import test from "node:test";
import assert from "node:assert/strict";
import {parseEnvelope, isLocalRequest} from "../src/server/runtime/validation.ts";

const envelope = () => ({requestId:"request-1", command:{type:"ingest", relationshipId:"rt-sofia-mx", sourceMessageId:"msg-1", kind:"request_card", text:"Hola, ya tengo el reloj.", mode:"live"}});
test("local runtime accepts bounded structured events without accepting arbitrary commands", () => {
  assert.equal(parseEnvelope(envelope()).command.kind, "request_card");
  for (const patch of [{sql:"SELECT * FROM secrets"}, {type:"shell"}, {relationshipId:"../../legacy"}, {text:""}, {text:"a".repeat(2001)}, {deferSeconds:20}]) {
    const e = envelope(); Object.assign(e.command, patch);
    assert.throws(() => parseEnvelope(e));
  }
});
test("versioned controls require a current integer revision", () => {
  const base = {requestId:"control-1", command:{type:"control", relationshipId:"rt-sofia-mx", mode:"human", expectedRevision:1}};
  assert.equal(parseEnvelope(base).command.expectedRevision, 1);
  for (const invalid of [undefined, -1, 0, 1.5, "1", Infinity]) {
    assert.throws(() => parseEnvelope({...base, command:{...base.command, expectedRevision:invalid}}));
  }
});
test("verification binds both the action and component, never a resend instruction", () => {
  const c = {type:"verify", relationshipId:"rt-pedro-br", actionId:"action-1", componentId:"component-1"};
  assert.deepEqual(parseEnvelope({requestId:"verify-1", command:c}).command, c);
  assert.throws(() => parseEnvelope({requestId:"verify-1", command:{...c, resend:true}}));
  assert.throws(() => parseEnvelope({requestId:"verify-1", command:{...c, componentId:undefined}}));
});
test("only explicit bounded waits accept a demo delay", () => {
  const e=envelope(); e.command.kind="ask_later"; e.command.deferSeconds=20;
  assert.equal(parseEnvelope(e).command.deferSeconds,20);
  for (const delay of [0,4,301,1.5,"20"]) assert.throws(() => parseEnvelope({...e,command:{...e.command,deferSeconds:delay}}));
});
test("mutations reject foreign origins, absent origin and DNS rebinding hosts", () => {
  const request = (host, origin, site="same-origin") => new Request("http://127.0.0.1:5198/api/local-runtime",{headers:{host,...(origin?{origin}:{}),"sec-fetch-site":site}});
  assert.equal(isLocalRequest(request("127.0.0.1:5198","http://127.0.0.1:5198"),true),true);
  assert.equal(isLocalRequest(request("localhost:5198","http://localhost:5198"),true),true);
  for (const req of [request("evil.example","http://evil.example"),request("127.0.0.1:5198","https://evil.example"),request("127.0.0.1:5198",undefined),request("127.0.0.1:5198","http://127.0.0.1:5198","cross-site")]) assert.equal(isLocalRequest(req,true),false);
  assert.equal(isLocalRequest(request("127.0.0.1:5198",undefined),false),true);
  assert.equal(isLocalRequest(request("evil.example",undefined),false),false);
});

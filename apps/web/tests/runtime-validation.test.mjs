import test from "node:test";
import assert from "node:assert/strict";
import {isLocalRequest} from "../src/server/runtime/validation.ts";

test("mutations reject foreign origins, absent origin and DNS rebinding hosts", () => {
  const request = (host, origin, site="same-origin") => new Request("http://127.0.0.1:5198/api/send",{headers:{host,...(origin?{origin}:{}),"sec-fetch-site":site}});
  assert.equal(isLocalRequest(request("127.0.0.1:5198","http://127.0.0.1:5198"),true),true);
  assert.equal(isLocalRequest(request("localhost:5198","http://localhost:5198"),true),true);
  for (const req of [request("evil.example","http://evil.example"),request("127.0.0.1:5198","https://evil.example"),request("127.0.0.1:5198",undefined),request("127.0.0.1:5198","http://127.0.0.1:5198","cross-site")]) assert.equal(isLocalRequest(req,true),false);
  assert.equal(isLocalRequest(request("127.0.0.1:5198",undefined),false),true);
  assert.equal(isLocalRequest(request("evil.example",undefined),false),false);
});

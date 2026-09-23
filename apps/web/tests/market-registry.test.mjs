import test from "node:test";
import assert from "node:assert/strict";
import {readMarketRegistry} from "../src/server/markets/registry.ts";

test("product markets expose Campaign everywhere and full-managed only where supported",()=>{
 const registry=readMarketRegistry(),byKey=Object.fromEntries(registry.markets.map(row=>[row.key,row]));
 assert.equal(registry.defaultMarket,"it");
 assert.deepEqual(registry.markets.map(row=>row.key),["be","br","de","it","jp","my","mx","nl","ph","sg","th","uk","us","vn"]);
 for(const market of registry.markets)assert.equal(market.capabilities.campaignCatalog,true);
 assert.equal(byKey.br.capabilities.fullManagedCatalog,false);
 assert.equal(byKey.my.capabilities.fullManagedCatalog,false);
 assert.equal(byKey.it.capabilities.fullManagedCatalog,true);
 assert.equal(byKey.uk.capabilities.fullManagedCatalog,true);
 assert.equal(byKey.be.capabilities.fullManagedCatalog,null);
 assert.equal(byKey.be.accounts.communications,null);
 assert.equal(byKey.be.runtimeState,"planned");
});

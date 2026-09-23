import test from "node:test";
import assert from "node:assert/strict";

const routes=["campaign-join","campaign-links","campaign-panel","catalog-jobs","catalog-names","catalog-screen","conversations","creator-discovery","creator-identities","creator-profile-refresh","cycle-service","global-source","identity-queue","inbox","jobs","kalodata-identity","lead-pool","leads-queue","link-naming","market-accounts","market-catalog","operations-home","reply-review","send","template-library","workflow"];

test("every active market GET rejects a missing market before invoking its backend",async()=>{
 for(const name of routes){
  const {GET}=await import(`../src/app/api/${name}/route.ts`);
  for(const query of ["","?market=it&market=br","?market=zz"]){
   const response=await GET(new Request(`http://127.0.0.1:5198/api/${name}${query}`,{headers:{host:"127.0.0.1:5198"}}));
   assert.equal(response.status,400,`${name} ${query}`);
  }
 }
});

test("unsupported full-managed market reports an empty state without running the catalog CLI",async()=>{
 const {GET}=await import("../src/app/api/market-catalog/route.ts");
 for(const market of ["br","my"]){
  const response=await GET(new Request(`http://127.0.0.1:5198/api/market-catalog?market=${market}&view=products`,{headers:{host:"127.0.0.1:5198"}}));
  assert.equal(response.status,200);
  assert.deepEqual(await response.json(),{market,availability:"unsupported",items:[],total:0,offset:0,limit:30,observedAt:null,readOnly:true,platformWrites:0});
 }
});

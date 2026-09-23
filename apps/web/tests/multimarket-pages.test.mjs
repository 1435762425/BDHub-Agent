import test from "node:test";
import assert from "node:assert/strict";
import {existsSync,readFileSync} from "node:fs";
import {readMarketRegistry} from "../src/server/markets/registry.ts";

const root=new URL("../src/",import.meta.url);
const read=path=>readFileSync(new URL(path,root),"utf8");

test("fourteen markets resolve the same eleven route contracts",()=>{
 const keys=readMarketRegistry().markets.map(row=>row.key);
 assert.deepEqual(keys,["be","br","de","it","jp","my","mx","nl","ph","sg","th","uk","us","vn"]);
 const suffixes=["","/workspace/send","/workspace/history","/conversations","/conversations/templates","/conversations/agent","/catalog","/creators","/ops/kalodata","/ops/jobs","/ops/accounts"];
 const routes=keys.flatMap(market=>suffixes.map(suffix=>`/${market}${suffix}`));
 assert.equal(routes.length,154);assert.equal(new Set(routes).size,154);
});

test("market routes use one workspace tree and keep every tab in place",()=>{
 const catalogRoute=read("app/[market]/catalog/page.tsx"),workspaceRoute=read("app/[market]/workspace/[section]/page.tsx");
 assert.match(catalogRoute,/CatalogWorkspace definition=/);assert.doesNotMatch(catalogRoute,/market\s*===\s*["']it/);
 assert.match(workspaceRoute,/SecondOutreachWorkspace market=/);assert.doesNotMatch(workspaceRoute,/MarketOutreachWorkspace|market\s*===\s*["']it/);
 const catalog=read("features/catalog/CatalogWorkspace.tsx");
 for(const label of ["全托商品","非全托商品","达人线索","新建链接命名"])assert.match(catalog,new RegExp(label));
 for(const label of ["发送","结果与历史"])assert.match(read("features/second-outreach/SecondOutreachWorkspace.tsx"),new RegExp(label));
 for(const label of ["会话","人工回复模板","Agent AI 回复设置"])assert.match(read("features/conversations/ConversationWorkspace.tsx"),new RegExp(label));
 for(const label of ["Kalodata 抓取身份","作业与定时","机构账号设置"])assert.match(read("features/ops/OpsWorkspace.tsx"),new RegExp(label));
});

test("retired IT versus market fork components are absent",()=>{
 for(const path of ["features/catalog/MarketCatalogWorkspace.tsx","features/markets/MarketOutreachWorkspace.tsx","features/markets/MarketContentPanel.tsx","features/markets/MarketActivationWorkspace.tsx"]){
  assert.equal(existsSync(new URL(path,root)),false,path);
 }
});

test("unsupported full managed tab exits before its API read",()=>{
 const source=read("features/catalog/CatalogWorkspace.tsx");
 assert.match(source,/if\(!supported\|\|!runtimeAvailable\)return/);
 assert.match(source,/该市场暂无全托商品/);
 assert.match(source,/全托能力尚未验收/);
});

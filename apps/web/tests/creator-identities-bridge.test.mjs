import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {execFile} from 'node:child_process';
import {createServer} from 'node:http';
import {mkdtempSync,readFileSync,writeFileSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {creatorIdentityReadCommand,createCreatorIdentityReader,readCreatorIdentities} from '../src/server/creator-identities/bridge.ts';
import {validateCreatorIdentityResult} from '../src/server/creator-identities/read-contract.ts';
import {GET} from '../src/app/api/creator-identities/route.ts';
import {projectRoot} from '../src/server/runtime/project-root.ts';

const ID='creator_'+'a'.repeat(32),OTHER='creator_'+'b'.repeat(32),AT='2026-10-24T01:00:00Z';
const OVERVIEW={view:'overview',market:'it'},LIST={view:'list',market:'it',status:'verified',q:'',offset:0,limit:10},DETAIL={view:'detail',market:'it',creatorId:ID},SOURCE={view:'source',market:'it',oecId:'123'};
const SCHEMA=readFileSync(new URL('../../../scripts/lib/creator_identity.py',import.meta.url),'utf8').match(/_SCHEMA = """([\s\S]*?)"""/)[1];
function fixture(t){
 const dir=mkdtempSync(join(tmpdir(),'identity-read-bridge-')),path=join(dir,'identity.sqlite'),cycle=join(dir,'missing-cycle.sqlite'),db=new DatabaseSync(path);db.exec(SCHEMA);
 db.prepare('INSERT INTO creator_identity VALUES(?,?,?,?,?,?,?,?,?,?)').run(ID,'it','123','alice',AT,1,0,AT,1,AT);
 db.prepare('INSERT INTO identity_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)').run('obs-a',ID,'it','123','profile','alice','observed',AT,1,'PRIVATE_EVIDENCE','PRIVATE_FINGERPRINT',JSON.stringify({fields:{follower_cnt:{status:'zero',value:0}},cookie:'SECRET'}));
 db.prepare('INSERT INTO handle_lead VALUES(?,?,?,?,?,?,?,?,?)').run('lead-a','it','pending',AT,'PRIVATE_EVIDENCE','PRIVATE_FINGERPRINT','probe_source_reference','external-a','{}');
 db.close();
 const oldIdentity=process.env.BDHUB_AGENT_IDENTITY_DB,oldCycle=process.env.BDHUB_AGENT_CYCLE_DB;
 process.env.BDHUB_AGENT_IDENTITY_DB=path;process.env.BDHUB_AGENT_CYCLE_DB=cycle;
 t.after(()=>{for(const [key,value] of [['BDHUB_AGENT_IDENTITY_DB',oldIdentity],['BDHUB_AGENT_CYCLE_DB',oldCycle]])if(value===undefined)delete process.env[key];else process.env[key]=value;rmSync(dir,{recursive:true,force:true});});
 return {dir,path,cycle};
}
function rawCli(input){
 const command=creatorIdentityReadCommand(projectRoot());
 return new Promise(resolve=>{const child=execFile(command.file,command.args,{cwd:command.cwd,timeout:5000,env:{...process.env}},(error,stdout)=>resolve({error,value:JSON.parse(stdout)}));child.stdin.end(input);});
}

test('fixed Node CLI keeps query data on stdin and preserves all four read-only view contracts',async t=>{
 const f=fixture(t),bytes=readFileSync(f.path),command=creatorIdentityReadCommand('/project');
 assert.equal(command.file,process.execPath);assert.deepEqual(command.args,['--experimental-strip-types','/project/scripts/read-creator-identities.mjs']);assert.equal(command.cwd,'/project/apps/web');
 const overview=await readCreatorIdentities(OVERVIEW);assert.equal(overview.verifiedIdentities,1);assert.equal(overview.repliedCreators,null);
 const page=await readCreatorIdentities(LIST);assert.equal(page.total,1);assert.equal(page.items[0].creatorId,ID);
 const detail=await readCreatorIdentities(DETAIL);assert.equal(detail.creator.creatorId,ID);assert.equal(detail.fields.length,22);assert.equal(detail.fields.find(row=>row.name==='follower_cnt').value,0);
 const source=await readCreatorIdentities(SOURCE);assert.equal(source.status,'verified');assert.equal(source.creator.oecId,'123');assert.equal(source.historicalCrossSourceIdentityProven,false);
 const pending=await readCreatorIdentities({view:'source',market:'it',externalId:'external-a'});assert.equal(pending.status,'pending');assert.equal(pending.creator,null);
 assert.doesNotMatch(JSON.stringify([overview,page,detail,source]),/PRIVATE_|SECRET|payload_json/);
 assert.deepEqual(readFileSync(f.path),bytes);assert.equal(existsSync(f.cycle),false);
});

test('missing datasets, not-found details, schema failure and local HTTP gates retain their outcomes',async t=>{
 const f=fixture(t);
 await assert.rejects(readCreatorIdentities({...DETAIL,creatorId:OTHER}),error=>error.status===404&&error.code==='creator_not_found');
 const response=await GET(new Request(`http://127.0.0.1:5198/api/creator-identities?view=detail&market=it&creatorId=${OTHER}`,{headers:{host:'127.0.0.1:5198'}}));assert.equal(response.status,404);
 for(const headers of [{host:'evil.invalid'},{host:'127.0.0.1:5198',origin:'https://foreign.invalid'}])assert.equal((await GET(new Request('http://127.0.0.1:5198/api/creator-identities?view=overview&market=it',{headers}))).status,403);
 process.env.BDHUB_AGENT_IDENTITY_DB=join(f.dir,'missing.sqlite');
 assert.equal((await readCreatorIdentities(OVERVIEW)).datasetStatus,'not_imported');assert.equal(existsSync(process.env.BDHUB_AGENT_IDENTITY_DB),false);
 const bad=join(f.dir,'bad.sqlite'),db=new DatabaseSync(bad);db.exec('CREATE TABLE private_cookie(secret TEXT)');db.close();process.env.BDHUB_AGENT_IDENTITY_DB=bad;
 await assert.rejects(readCreatorIdentities(OVERVIEW),error=>error.status===503&&error.code==='identity_schema_mismatch'&&!error.message.includes('private_cookie'));
});

test('CLI rejects malformed stdin, hidden paths, unknown fields and unvalidated scalar types',async t=>{
 fixture(t);
 for(const input of ['{broken',JSON.stringify({...OVERVIEW,database:'/private/secret'}),JSON.stringify({view:'__proto__',market:'it'}),JSON.stringify({...LIST,offset:true}),' '.repeat(4097)]){
  const result=await rawCli(input);assert.equal(result.value.ok,false);assert.equal(result.value.error.status,400);assert.equal(result.value.error.code,'invalid_request');assert.doesNotMatch(JSON.stringify(result.value),/private|secret|stack/);
 }
});

test('decoder rejects market, ID, enum and count contradictions without manufacturing unavailable zeroes',async t=>{
 fixture(t);
 const overview=await readCreatorIdentities(OVERVIEW),page=await readCreatorIdentities(LIST),detail=await readCreatorIdentities(DETAIL),source=await readCreatorIdentities(SOURCE);
 for(const [query,value] of [
  [OVERVIEW,{...overview,market:'br'}],[OVERVIEW,{...overview,verifiedIdentities:-1}],[OVERVIEW,{...overview,markets:[]}],
  [LIST,{...page,limit:20}],[LIST,{...page,total:0}],[LIST,{...page,items:[{...page.items[0],creatorId:'different'}]}],
  [DETAIL,{...detail,creator:{...detail.creator,creatorId:OTHER}}],[DETAIL,{...detail,fields:[{name:'cookie',status:'value',observedAt:AT,value:'SECRET'}]}],
  [SOURCE,{...source,status:'approved'}],[SOURCE,{...source,candidateCount:2}],[SOURCE,{...source,historicalCrossSourceIdentityProven:true}],
 ])assert.throws(()=>validateCreatorIdentityResult(value,query),error=>error.code==='identity_unavailable');
});

test('a slow synchronous child read leaves HTTP heartbeats and the parent event loop responsive',async t=>{
 const f=fixture(t),preload=join(f.dir,'slow-read.mjs');writeFileSync(preload,'Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,400);');
 let spawned;const ready=new Promise(resolve=>{spawned=resolve;});
 const reader=createCreatorIdentityReader((binary,args,options,callback)=>{const child=execFile(binary,['--import',pathToFileURL(preload).href,...args],options,callback);child.once('spawn',spawned);return child;});
 const server=createServer(async(request,response)=>{if(request.url==='/identity'){try{response.end(JSON.stringify(await reader(OVERVIEW)));}catch{response.statusCode=503;response.end();}}else response.end('pong');});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
 const url=`http://127.0.0.1:${server.address().port}`;
 let ticks=0;const timer=setInterval(()=>ticks++,10);t.after(()=>clearInterval(timer));
 const slow=fetch(url+'/identity').then(response=>response.json());await ready;
 const pulse=fetch(url+'/health').then(response=>response.text());
 assert.equal(await Promise.race([slow.then(()=>'identity'),pulse.then(()=>'health')]),'health');assert.equal(await pulse,'pong');
 assert.equal((await slow).verifiedIdentities,1);assert.ok(ticks>0);
});

test('timeout and request cancellation kill only the read child and return unavailable',async t=>{
 const f=fixture(t),preload=join(f.dir,'blocked-read.mjs');writeFileSync(preload,'Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,5000);');
 for(const cancel of [false,true]){
  let signal,exited;const ready=new Promise(resolve=>{signal=resolve;}),closed=new Promise(resolve=>{exited=resolve;});let killedWith;
  const reader=createCreatorIdentityReader((binary,args,options,callback)=>{const child=execFile(binary,['--import',pathToFileURL(preload).href,...args],{...options,timeout:cancel?5000:50},callback);child.once('spawn',signal);child.once('exit',(_code,kind)=>{killedWith=kind;});child.once('close',exited);return child;});
  const controller=new AbortController(),result=reader(OVERVIEW,controller.signal);await ready;if(cancel)controller.abort();
  await assert.rejects(result,error=>error.status===503&&error.code==='identity_unavailable');await closed;assert.equal(killedWith,'SIGKILL');
 }
});

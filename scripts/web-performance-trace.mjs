#!/usr/bin/env node
/** Local Chrome/CDP performance trace for the market workbench. No external network is used. */
import {spawn} from "node:child_process";
import {mkdtemp,rm,writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";

const args=Object.fromEntries(process.argv.slice(2).map(value=>{const [key,...rest]=value.replace(/^--/,"").split("=");return [key,rest.join("=")||"true"];}));
const output=args.output;if(!output)throw Error("--output is required");
const runs=Math.max(1,Math.min(10,Number(args.runs||3)));
const origin=String(args.origin||"http://127.0.0.1:5198").replace(/\/$/,"");
const paths=(args.paths?String(args.paths).split(","):["/it","/it/workspace/send","/it/catalog","/it/creators","/uk/catalog","/br/catalog"]);
const port=Number(args.port||9333),profile=await mkdtemp(join(tmpdir(),"bdhub-chrome-trace-"));
const chrome=spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",[
 "--headless=new",`--remote-debugging-port=${port}`,`--user-data-dir=${profile}`,"--disable-background-networking","--disable-component-update","--disable-default-apps","--disable-extensions","--no-first-run","about:blank",
],{stdio:"ignore"});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitJson(path){for(let i=0;i<80;i++){try{const response=await fetch(`http://127.0.0.1:${port}${path}`);if(response.ok)return response.json();}catch{}await sleep(100);}throw Error("chrome_debug_unavailable");}
class CDP{
 constructor(url){this.ws=new WebSocket(url);this.id=0;this.pending=new Map();this.listeners=new Map();this.ready=new Promise((resolve,reject)=>{this.ws.addEventListener("open",resolve,{once:true});this.ws.addEventListener("error",reject,{once:true});});this.ws.addEventListener("message",event=>{const message=JSON.parse(String(event.data));if(message.id){const pending=this.pending.get(message.id);if(pending){this.pending.delete(message.id);message.error?pending.reject(Error(message.error.message)):pending.resolve(message.result);}}else for(const listener of this.listeners.get(message.method)||[])listener(message.params);});}
 async send(method,params={}){await this.ready;const id=++this.id;return new Promise((resolve,reject)=>{this.pending.set(id,{resolve,reject});this.ws.send(JSON.stringify({id,method,params}));});}
 once(method){return new Promise(resolve=>{const handler=value=>{this.listeners.set(method,(this.listeners.get(method)||[]).filter(item=>item!==handler));resolve(value);};this.listeners.set(method,[...(this.listeners.get(method)||[]),handler]);});}
 close(){this.ws.close();}
}
const bootstrap=`(()=>{window.__bdhubPerf={lcp:0,cls:0,tbt:0,longTasks:0};try{new PerformanceObserver(list=>{for(const e of list.getEntries())window.__bdhubPerf.lcp=Math.max(window.__bdhubPerf.lcp,e.startTime)}).observe({type:'largest-contentful-paint',buffered:true})}catch{}try{new PerformanceObserver(list=>{for(const e of list.getEntries())if(!e.hadRecentInput)window.__bdhubPerf.cls+=e.value}).observe({type:'layout-shift',buffered:true})}catch{}try{new PerformanceObserver(list=>{for(const e of list.getEntries()){window.__bdhubPerf.longTasks++;window.__bdhubPerf.tbt+=Math.max(0,e.duration-50)}}).observe({type:'longtask',buffered:true})}catch{}})();`;
async function trace(url){
 const target=await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent("about:blank")}`,{method:"PUT"}).then(response=>response.json());const cdp=new CDP(target.webSocketDebuggerUrl);await cdp.ready;
 let bytes=0,requests=0;const apiRequests=[];cdp.listeners.set("Network.requestWillBeSent",[event=>{requests++;if(event.request.url.startsWith(origin+"/api/")){const url=new URL(event.request.url);apiRequests.push(url.pathname+url.search);}}]);cdp.listeners.set("Network.loadingFinished",[event=>{bytes+=Number(event.encodedDataLength||0);}]);
 await Promise.all([cdp.send("Page.enable"),cdp.send("Network.enable"),cdp.send("Performance.enable"),cdp.send("Accessibility.enable")]);await cdp.send("Page.addScriptToEvaluateOnNewDocument",{source:bootstrap});
 const loaded=cdp.once("Page.loadEventFired");await cdp.send("Page.navigate",{url});await loaded;await sleep(5000);
 const expression=`(()=>{const n=performance.getEntriesByType('navigation')[0],p=Object.fromEntries(performance.getEntriesByType('paint').map(e=>[e.name,e.startTime])),r=performance.getEntriesByType('resource');return {url:location.href,ttfb:n?n.responseStart:0,domContentLoaded:n?n.domContentLoadedEventEnd:0,load:n?n.loadEventEnd:0,fcp:p['first-contentful-paint']??null,resourceStable:r.length?Math.max(...r.map(e=>e.responseEnd)):0,...(window.__bdhubPerf||{})}})()`;
 const evaluated=await cdp.send("Runtime.evaluate",{expression,returnByValue:true});const metrics=evaluated.result.value;const ax=await cdp.send("Accessibility.getFullAXTree");const perf=await cdp.send("Performance.getMetrics");
 cdp.close();await fetch(`http://127.0.0.1:${port}/json/close/${target.id}`);
 return {...metrics,encodedBytes:bytes,requests,apiRequests,axNodes:ax.nodes.length,jsHeapUsed:perf.metrics.find(row=>row.name==="JSHeapUsedSize")?.value??null};
}
const samples=[];
try{await waitJson("/json/version");for(const path of paths)for(let run=1;run<=runs;run++)samples.push({path,run,...await trace(origin+path)});const percentile=values=>{const clean=values.filter(Number.isFinite).sort((a,b)=>a-b);return clean.length?clean[Math.min(clean.length-1,Math.ceil(clean.length*.95)-1)]:null;};const summary=paths.map(path=>{const rows=samples.filter(row=>row.path===path);return {path,runs:rows.length,ttfbP95:percentile(rows.map(row=>row.ttfb)),fcpP95:percentile(rows.map(row=>row.fcp)),lcpP95:percentile(rows.map(row=>row.lcp)),tbtP95:percentile(rows.map(row=>row.tbt)),clsP95:percentile(rows.map(row=>row.cls)),resourceStableP95:percentile(rows.map(row=>row.resourceStable)),requestsP95:percentile(rows.map(row=>row.requests)),encodedBytesP95:percentile(rows.map(row=>row.encodedBytes)),axNodesP95:percentile(rows.map(row=>row.axNodes))};});await writeFile(output,JSON.stringify({schema:"bdhub.web-performance-trace.v1",createdAt:new Date().toISOString(),origin,runs,summary,samples},null,2)+"\n");process.stdout.write(JSON.stringify({output,summary})+"\n");}
finally{chrome.kill("SIGTERM");await rm(profile,{recursive:true,force:true});}

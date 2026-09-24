import type {JobsState,WorkbenchJob} from "../../server/jobs/bridge.ts";

type Fetcher=(input:string,init?:RequestInit)=>Promise<Response>;
async function json(fetcher:Fetcher,url:string,body?:unknown) {
  const response=await fetcher(url,body===undefined?{cache:"no-store"}:{
    method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),
  });
  if(!response.ok)throw Error("job_time_unavailable");
  return response.json();
}

/** Runtime windows have one market-specific authority; never shadow-save them to global jobs. */
export async function saveJobTime(
  market:string,job:WorkbenchJob,at:string,weekday:number,
  fetcher:Fetcher=fetch,newId=()=>crypto.randomUUID(),
):Promise<{state:JobsState|null}> {
  const query=`?market=${encodeURIComponent(market)}`;
  if(job.id==="agent_reply") {
    const library=await json(fetcher,`/api/template-library${query}`);
    const {revision,updatedAt:_,...setting}=library.agentSetting;
    // Moving the start keeps the saved window length; one hour applies only when the saved window is unusable.
    const clock=(value:string)=>{const [h,m]=value.split(":").map(Number);return h*60+m;};
    const length=clock(setting.replyEnd)-clock(setting.replyStart);
    const [hour,minute]=at.split(":").map(Number),endMinutes=Math.min(1440,hour*60+minute+(length>0?length:60));
    setting.replyStart=at;
    setting.replyEnd=endMinutes===1440?"24:00":`${String(Math.floor(endMinutes/60)).padStart(2,"0")}:${String(endMinutes%60).padStart(2,"0")}`;
    await json(fetcher,`/api/template-library${query}`,{action:"save_agent",market,expectedRevision:revision,setting});
  } else if(job.id==="continuous_send") {
    const state=await json(fetcher,`/api/send${query}`);
    await json(fetcher,`/api/send${query}`,{action:"save",market,requestId:`send-time-${newId()}`,
      expectedRevision:state.control.revision,changes:{window:[at,state.control.window[1]]}});
  } else {
    const patch={at,...(job.cadence==="weekly"?{weekday}:{})};
    const state=await json(fetcher,`/api/jobs${query}`,{action:"save",market,jobs:{[job.id]:patch}});
    return {state};
  }
  // The mutation already succeeded. A failed readback must not be reported as a failed save.
  try{return {state:await json(fetcher,`/api/jobs${query}`)};}
  catch{return {state:null};}
}

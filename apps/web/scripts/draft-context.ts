import {normalizeDraftContextRequest,DraftContextError} from "../src/server/outreach-drafts/context.ts";
import {compileOutreachContext} from "../src/server/outreach-drafts/compile.ts";
import {SecondOutreachError} from "../src/server/second-outreach/bridge.ts";
import {MatchingError} from "../src/server/matching/store.ts";
let source="";
try{
  if(process.argv.length>2)throw new DraftContextError("invalid_request","Unsupported arguments",400);
  for await(const chunk of process.stdin){source+=chunk.toString();if(Buffer.byteLength(source)>8192)throw new DraftContextError("invalid_request","Input too large",400);}
  const result=await compileOutreachContext(normalizeDraftContextRequest(JSON.parse(source)));process.stdout.write(JSON.stringify(result));
}catch(error){
  const known=error instanceof DraftContextError||error instanceof MatchingError||error instanceof SecondOutreachError;
  process.stdout.write(JSON.stringify({error:{code:known?error.code:"context_unavailable",status:known?error.status:503,message:known?error.message:"Draft context unavailable"}}));process.exitCode=1;
}

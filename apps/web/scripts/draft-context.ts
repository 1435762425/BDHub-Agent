import {buildDraftContext,normalizeDraftContextRequest,DraftContextError} from "../src/server/outreach-drafts/context.ts";
import {MatchingError} from "../src/server/matching/store.ts";
let source="";
try{
  if(process.argv.length>2)throw new DraftContextError("invalid_request","Unsupported arguments",400);
  for await(const chunk of process.stdin){source+=chunk.toString();if(Buffer.byteLength(source)>8192)throw new DraftContextError("invalid_request","Input too large",400);}
  const result=buildDraftContext(normalizeDraftContextRequest(JSON.parse(source)));process.stdout.write(JSON.stringify(result));
}catch(error){
  const known=error instanceof DraftContextError||error instanceof MatchingError;
  process.stdout.write(JSON.stringify({error:{code:known?error.code:"context_unavailable",status:known?error.status:503,message:known?error.message:"Draft context unavailable"}}));process.exitCode=1;
}

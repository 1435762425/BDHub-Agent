/** Read-only protocol boundary. The existing Store remains the only SQL/projection implementation. */
import {getCreatorIdentityStore} from '../../apps/web/src/server/creator-identities/instance.ts';
import {CreatorIdentityError} from '../../apps/web/src/server/creator-identities/errors.ts';
import {parseCreatorIdentityQuery} from '../../apps/web/src/server/creator-identities/validation.ts';
import {InputError} from '../../apps/web/src/server/runtime/validation.ts';

export function parseQuery(raw){
  if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new InputError('身份查询格式不正确。');
  const views={overview:['view','market'],list:['view','market','status','q','offset','limit'],detail:['view','market','creatorId'],source:['view','market','oecId','externalId']};
  const allowed=typeof raw.view==='string'&&Object.hasOwn(views,raw.view)?views[raw.view]:null;
  if(!allowed||Object.keys(raw).some(key=>!allowed.includes(key))||!Object.hasOwn(raw,'market'))throw new InputError('身份查询格式不正确。');
  const params=new URLSearchParams();
  for(const [key,value] of Object.entries(raw)){
    if(key==='offset'||key==='limit'){
      if(typeof value!=='number'||!Number.isSafeInteger(value)||value<0)throw new InputError('身份查询格式不正确。');
    }else if(typeof value!=='string')throw new InputError('身份查询格式不正确。');
    params.set(key,String(value));
  }
  return parseCreatorIdentityQuery(`http://127.0.0.1:5198/api/creator-identities?${params}`);
}

export function read(query){
  const store=getCreatorIdentityStore();
  try{
    const result=query.view==='overview'?store.overview(query.market):query.view==='list'?store.list(query):query.view==='detail'?store.detail(query.market,query.creatorId):store.source(query);
    if(query.view==='detail'&&result.creator===null)throw new CreatorIdentityError(404,'creator_not_found','当前市场没有这位达人。');
    return result;
  }finally{store.close();}
}

export async function main(){
  let size=0;const chunks=[];
  try{
    for await(const chunk of process.stdin){size+=chunk.length;if(size>4096)throw new InputError('身份查询格式不正确。');chunks.push(chunk);}
    const query=parseQuery(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    process.stdout.write(JSON.stringify({ok:true,data:read(query)}));
  }catch(error){
    const known=error instanceof InputError||error instanceof CreatorIdentityError;
    const status=known?error.status:error instanceof SyntaxError?400:503,code=known?error.code:error instanceof SyntaxError?'invalid_request':'identity_unavailable';
    process.stdout.write(JSON.stringify({ok:false,error:{status,code}}));
    process.exitCode=1;
  }
}

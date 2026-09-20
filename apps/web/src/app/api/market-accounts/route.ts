import {createAccountsGet,mutateAccount,readAccounts,validateAccountMutation} from '../../../server/market-accounts/bridge.ts';
import {isLocalRequest} from '../../../server/runtime/validation.ts';
export const runtime='nodejs';
export const dynamic='force-dynamic';
export const GET=createAccountsGet();
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};
export async function POST(request:Request){if(!isLocalRequest(request,true))return Response.json({error:'local_origin_required'},{status:403,headers});let input;try{if(request.headers.get('content-type')?.split(';')[0].trim()!=='application/json')return Response.json({error:'json_required'},{status:415,headers});const raw=await request.text();if(new TextEncoder().encode(raw).length>4096)throw Error();input=validateAccountMutation(JSON.parse(raw));}catch{return Response.json({error:'invalid_account_request'},{status:400,headers});}try{await mutateAccount(input);return Response.json(await readAccounts(),{headers});}catch(error){const code=error instanceof Error?error.message:'account_mutation_unavailable';return Response.json({error:code},{status:code.includes('conflict')||code.includes('active')?409:503,headers});}}

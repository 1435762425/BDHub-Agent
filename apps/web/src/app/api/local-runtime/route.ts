import {getLocalRuntime} from "@/server/runtime/instance";
import {InputError, isLocalRequest, parseEnvelope} from "@/server/runtime/validation";
import {RuntimeError} from "@/server/runtime/engine";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = {"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"};
const rejected = () => Response.json({error:{code:"local_origin_required", message:"本地运行接口只接受本机工作台请求。"}}, {status:403, headers});
function failure(error: unknown) {
  if (error instanceof InputError || error instanceof RuntimeError) return Response.json({error:{code:error.code, message:error.message}}, {status:error.status, headers});
  console.error("Local runtime operation failed", error instanceof Error ? error.name : "unknown");
  return Response.json({error:{code:"runtime_unavailable", message:"本地运行暂不可用。请保留当前请求，检查运行进程后重试。"}}, {status:503, headers});
}
export async function GET(request: Request) {
  if (!isLocalRequest(request, false)) return rejected();
  try { return Response.json(getLocalRuntime().snapshot(), {headers}); }
  catch (error) { return failure(error); }
}
export async function POST(request: Request) {
  if (!isLocalRequest(request, true)) return rejected();
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") return Response.json({error:{code:"json_required", message:"请使用 JSON 请求。"}}, {status:415, headers});
  try {
    const reader = request.body?.getReader();
    if (!reader) throw new InputError("请求内容为空。");
    const chunks: Uint8Array[] = [];
    let length = 0;
    try {
      for (;;) {
        const {done, value} = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > 16_384) { await reader.cancel(); throw new InputError("请求内容过长。"); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    let value: unknown;
    try { value = JSON.parse(new TextDecoder("utf-8", {fatal:true}).decode(bytes)); }
    catch { throw new InputError("请求不是有效的 JSON。"); }
    const {requestId, command} = parseEnvelope(value);
    return Response.json(getLocalRuntime().command(command, requestId), {headers});
  } catch (error) { return failure(error); }
}

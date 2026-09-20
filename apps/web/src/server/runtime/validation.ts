export class InputError extends Error {
  readonly status = 400;
  readonly code = "invalid_request";
}
const LOCAL_HOSTS = new Set(["127.0.0.1:5198", "localhost:5198"]);
export function isLocalRequest(request: Request, mutation: boolean): boolean {
  const host = request.headers.get("host");
  if (!host || !LOCAL_HOSTS.has(host)) return false;
  const origin = request.headers.get("origin");
  if (mutation && origin !== `http://${host}`) return false;
  if (origin && origin !== `http://${host}`) return false;
  if (request.headers.get("sec-fetch-site") === "cross-site") return false;
  return true;
}

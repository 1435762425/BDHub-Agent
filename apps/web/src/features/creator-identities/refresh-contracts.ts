export interface ProfileRefreshJob {
  id:string; creatorId:string; market:string; oecId:string;
  status:"queued"|"running"|"completed"|"blocked";
  createdAt:string; startedAt:string|null; finishedAt:string|null;
  errorCode:string|null; identityUpdated:boolean; requestCount:number|null;
}
export interface ProfileRefreshJobs {jobs:ProfileRefreshJob[];workerOnline:boolean;}
export interface ProfileRefreshRequest {market:string;creatorId:string;requestId:string;}

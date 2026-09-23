import type {ContinuousSendState} from "./send-contracts.ts";

export type SendDraft={automaticEnabled:boolean;window:[string,string];template:string};
export type SendEditor={data:ContinuousSendState|null;draft:SendDraft|null;conflict:boolean};
export const emptySendEditor:SendEditor={data:null,draft:null,conflict:false};

export function draftFromState(data:ContinuousSendState):SendDraft {
  return {automaticEnabled:data.control.automaticEnabled,window:[...data.control.window],template:data.control.template};
}
export function sameSendDraft(left:SendDraft|null,right:SendDraft|null):boolean {
  return !!left && !!right && left.automaticEnabled===right.automaticEnabled && left.template===right.template &&
    left.window[0]===right.window[0] && left.window[1]===right.window[1];
}

/** Polls update runtime facts while preserving an operator's unsaved settings. */
export function receiveSendState(current:SendEditor,data:ContinuousSendState,saved=false):SendEditor {
  if(current.data && current.data.control.revision>data.control.revision)return current;
  const dirty=current.data && current.draft && !sameSendDraft(current.draft,draftFromState(current.data));
  const revisionChanged=current.data && current.data.control.revision!==data.control.revision;
  return {data,draft:saved||!dirty?draftFromState(data):current.draft,
    conflict:!saved&&!!dirty&&(current.conflict||!!revisionChanged)};
}

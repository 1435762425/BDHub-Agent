/**
 * 发送池与发送：页面和桥接层共用的常量与类型。
 *
 * 放这里是因为客户端组件不能 import 服务端桥接（那会把 `node:child_process` 打进浏览器包）。
 * 桥接层反过来 import 这个文件没有问题。
 */

/** 只放行 500/1000 两档；`--widen` 时多一档 600（账号级日额度未知，先按它探）。 */
export const SEND_COUNTS=[500,1000];
export const PROBE_COUNT=600;

/** 这几条不是卡点，是"这一批装不下/还没到点"，页面要把它们跟真卡点分开说。 */
export const NOT_A_BLOCKER=new Set(["beyond_requested_size","local_capacity_reached","outside_send_window"]);

/** 页面能改的只有三件。`window` 用 `24:00` 表示当天结束。 */
export type SendConfig={count:number;widen:boolean;windowEnabled:boolean;window:[string,string]};
export type SendSample={handle:string;pid:string;name:string;nameZh:string;nameSource:string;
 messageIt:string;messageZh:string;template:string;
 creatorPercent:string;publicPercent:string;campaignId:string;catalogSource:string;unlocked:boolean};
export type SendCapacity={windowSeconds:number;limit:number;used:number;remaining:number};
export type SendWindow={enabled:boolean;open:boolean;start:string|null;end:string|null};
export type SendAuthorization={source:"current_user_request";scope:"pool_to_send";maxPeople:number;
 requestedPeople:number;widenLocalGate:boolean;sendWindow:[string,string]|null;
 institutionNewContactRollingCap:number;materialPolicy:"frozen-current-binding-v1";note:string};
/** 台账里的卡与**当前计划**的佣金差距：差 1 点的那批是旧卡（`🚀 Incentivo Boost` 那套名字）。 */
export type SendRateGap={same:number;lower:number;lowerByOne:number;higher:number;noCard:number;
 examples:{pid:string;listName:string;cardPercent:string;planPercent:string;campaignId:string}[]};
export type SendPreview={available:boolean;requested:number;sendable:number;positions:number;
 readyAvailable:number;samples:SendSample[];nameQuality:Record<string,number>;
 skipped:Record<string,number>;rateGap:SendRateGap;capacity:SendCapacity|null;window:SendWindow;widen:boolean;
 previewHash:string|null;authorization:SendAuthorization|null};
export type SendBatch={batchId:string;requestId:string;previewHash:string;revision:number;state:string;
 target:number;counts:Record<string,number>;config:SendConfig;authorization:SendAuthorization;
 authorizedAt:number|null;stopRequestedAt:number|null;createdAt:number;
 runtime:{pid:number|null;seenAt:number;phase:string}|null;workerPid?:number;duplicate?:boolean};
export type SendState={available:boolean;config:SendConfig;preview:SendPreview;
 pool:{counts:Record<string,number>;layers:Record<string,number>};batch:SendBatch|null;configInvalid?:boolean};

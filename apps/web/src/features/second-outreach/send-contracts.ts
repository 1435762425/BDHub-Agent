/**
 * 发送池与发送：页面和桥接层共用的常量与类型。
 *
 * 放这里是因为客户端组件不能 import 服务端桥接（那会把 `node:child_process` 打进浏览器包）。
 * 桥接层反过来 import 这个文件没有问题。
 */

/** 常用快捷档；实际目标允许 1–2000，600 是账号级日额度探测的快捷值。 */
export const SEND_COUNTS=[500,1000];
export const PROBE_COUNT=600;
export const SEND_TEMPLATE_IDS=["standard","brief","reconnect","video_focus","live_focus"] as const;
export type SendTemplateId=typeof SEND_TEMPLATE_IDS[number];
export type SendTemplateOption={id:SendTemplateId;label:string;description:string};

/** 这几条不是卡点，是"这一批装不下/还没到点"，页面要把它们跟真卡点分开说。 */
export const NOT_A_BLOCKER=new Set(["beyond_requested_size","local_capacity_reached","outside_send_window"]);

/** 页面能改人数、模板、窗口和是否越过本地闸门。`window` 用 `24:00` 表示当天结束。 */
export type SendConfig={count:number;widen:boolean;windowEnabled:boolean;window:[string,string];template:SendTemplateId};
export type SendSample={handle:string;oecId:string;pid:string;sourceClass:"A"|"B";sourceRank:number|null;units:number|null;
 gmv:string|null;videoViews:number|null;videoId:string|null;videoReleasedAt:string|null;name:string;nameZh:string;nameSource:string;
 messageIt:string;messageZh:string;template:string;
 creatorPercent:string;publicPercent:string;campaignId:string;catalogSource:string;currentListId:string;unlocked:boolean};
export type SendCapacity={windowSeconds:number;limit:number;used:number;remaining:number};
export type SendWindow={enabled:boolean;open:boolean;start:string|null;end:string|null};
export type SendAuthorization={source:"current_user_request";scope:"pool_to_send";maxPeople:number;
 requestedPeople:number;reservePeople:number;frozenPeople:number;widenLocalGate:boolean;sendWindow:[string,string]|null;
 reservePolicy:"ceil-10-percent-v1"|"none";
 messageTemplate:SendTemplateId;
 institutionNewContactRollingCap:number;materialPolicy:"frozen-current-binding-v1";note:string};
export type SendUnknownDelivery={deliveryId:string;creatorId:string;oecId:string;pid:string;parts:Record<string,string>};
/** 台账里的卡与**当前计划**的佣金差距：差 1 点的那批是旧卡（`🚀 Incentivo Boost` 那套名字）。 */
export type SendRateGap={same:number;lower:number;lowerByOne:number;higher:number;noCard:number;
 examples:{pid:string;listName:string;cardPercent:string;planPercent:string;campaignId:string}[]};
export type SendPreview={available:boolean;requested:number;reserveRequested:number;required:number;
 sendable:number;reserveReady:number;frozenTotal:number;fullPreparation:boolean;positions:number;
 readyAvailable:number;samples:SendSample[];nameQuality:Record<string,number>;
 skipped:Record<string,number>;rateGap:SendRateGap;capacity:SendCapacity|null;window:SendWindow;widen:boolean;
 previewHash:string|null;authorization:SendAuthorization|null};
export type SendBatch={batchId:string;requestId:string;previewHash:string;revision:number;state:string;
 target:number;attempted:number;reserveTotal:number;reservePromoted:number;reserveRemaining:number;
 counts:Record<string,number>;config:SendConfig;authorization:SendAuthorization;
 authorizedAt:number|null;stopRequestedAt:number|null;createdAt:number;unknownDeliveries:SendUnknownDelivery[];
 runtime:{pid:number|null;seenAt:number;phase:string}|null;workerPid?:number;duplicate?:boolean};
export type SendState={market:"it";account:"acc6";available:boolean;config:SendConfig;preview:SendPreview;
 templates:SendTemplateOption[];pool:{counts:Record<string,number>;layers:Record<string,number>};batch:SendBatch|null;configInvalid?:boolean};

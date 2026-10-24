const labels:Record<string,string>={
 queued:"已排队",waiting:"等待后自动继续",waiting_transport:"连接恢复中",inflight:"原申请提交中",accepted:"平台已受理，等待核验",rejected:"平台未通过原审批，留待处理",complete:"本轮已完成",partial:"部分商品等待，其他继续",ready_to_ship:"等待商家发货",content_pending:"等待达人发布",completed:"已完成",shipped:"已发货",cancelled:"已取消",goal_observed:"平台已进入后续阶段，原审批回执未确认",
 sample_authentication_required:"等待账号恢复登录",sample_network_unavailable:"平台连接暂不可用，将自动重试",sample_permission_denied:"平台尚未授予当前账号权限",sample_remote_read_failed:"样品读取暂时失败，将自动重试",waiting_original_identity:"等待原审批账号身份恢复后核验",sample_canary_lifecycle_pending:"首条审批正在等待平台后续状态",first_supply_workflow_busy:"原商品任务正在执行，完成后自动继续",first_supply_workflow_running:"原商品任务正在执行，完成后自动继续",first_category_read_budget:"分类正按批次自动推进",first_prepare_read_budget:"达人匹配正按批次自动推进",
 first_material_waiting_workflow:"原商品任务正在执行，完成后自动继续",first_material_timeout:"推荐卡准备暂未完成，将核验原记录后继续",first_material_response_invalid:"推荐卡准备暂未完成，将核验原记录后继续",first_material_incomplete:"推荐卡正在按批次自动准备",first_names_read_budget:"葡语商品名正在按批次自动准备",first_catalog_chunk_incomplete:"商品信息正在按批次自动更新",first_catalog_read_budget:"商品信息正在按批次自动更新",
 sample_no_eligible_first_application:"等待本功能成功推荐且仍符合规则的新申请",sample_canary_already_started:"首条审批正在核验，确认后自动继续",sample_canary_lifecycle_unconfirmed:"首条审批已提交，等待平台后续状态",sample_verification_required:"平台验证尚未完成，将自动重试",sample_login_required:"等待账号自动恢复登录",sample_rate_limited:"平台暂时限流，将稍后重试",sample_network_error:"平台连接暂不可用，将自动重试",sample_waiting_original_identity:"等待原审批账号身份恢复后核验",sample_original_identity_changed:"等待原审批账号身份恢复后核验",taplink_remote_read_failed:"平台读取暂时失败，将自动重试",market_send_result_unknown:"正在核验巴西原操作，核验后自动继续",sample_read_failed:"样品同步暂时失败，将自动重试",sample_read_verification_required:"平台验证尚未完成，将自动重试",sample_read_network_error:"平台连接暂不可用，将自动重试",
 stopped:"已停止",off:"尚未启动",starting:"正在启动",preparing:"正在准备匹配",syncing:"正在同步商品与申请",running:"正在执行",sending:"正在推送",continuous:"持续推送中",canary:"正在验证首条",canary_confirmed:"首条已确认，等待继续",canary_complete:"首条已确认，等待继续",canary_completed:"首条已确认，等待继续",
 account_busy:"等待账号空闲后继续",first_catalog_account_busy:"等待商品读取账号空闲后继续",workflow_resource_busy:"等待平台读取资源空闲后继续",workflow_stage_already_claimed:"当前步骤正在执行，等待原任务完成",sample_account_in_use:"样品账号正在使用，等待空闲后继续",first_resource_child_running:"原读取进程仍在执行，等待完成",ProfileBusyError:"账号正在使用，等待空闲后继续",live_guard_busy:"账号正在使用，等待空闲后继续",delivery_executor_busy:"原推送正在处理，等待结果",local_store_busy:"本机状态正在更新，稍后继续",outside_send_window:"等待发送时间窗口",seller_pending:"等待商家确认",closed_externally:"申请已在平台关闭",sample_application_coverage_unverified:"待审批范围尚未核实",quarantined_unknown:"原操作结果仍未知，已隔离并留待处理",
 waiting_reconciliation:"结果未知，等待核验",unknown:"结果未知，等待核验",waiting_pool:"等待可推荐达人",waiting_candidates:"等待可推荐达人",waiting_products:"等待可推荐商品",waiting_account:"等待账号空闲",waiting_capacity:"等待平台额度",waiting_cooldown:"等待72小时冷却结束",waiting_authorization:"等待页面启动",not_started:"等待页面启动",ready:"可以开始首条验证",idle:"等待下一步",attention:"需要处理",failed:"执行失败",blocked:"暂未就绪",
 first_outreach_prepare_required:"商品与达人匹配待准备",first_outreach_catalog_changed:"商品或样品信息已变化，匹配待更新",first_outreach_matches_unavailable:"匹配结果暂不可用，等待更新",first_outreach_result_unknown:"有结果未知的原操作，等待核验",
 first_outreach_materials_unavailable:"匹配商品的推荐链接或葡语商品名尚未就绪",first_outreach_send_capability_unverified:"巴西发送账号的发送能力尚未验证",first_outreach_worker_unavailable:"执行进程未运行，请重试原操作以继续",first_outreach_job_pending:"正在执行准备或同步任务，请等待完成",first_outreach_job_stopped:"准备或同步已停止，可重新发起",first_category_taxonomy_unavailable:"尚未取得可用的商品类目词表",first_category_incomplete:"部分商品的类目尚未确认",first_category_model_unavailable:"商品类目判断未完成，请检查模型运行状态",sample_review_contract_unverified:"样品审批能力尚未验证，申请保留待处理",sample_application_assessment_incomplete:"已展示申请完成资格核对，其他申请资格待核对",sample_review_result_unknown:"原审批结果未知，等待核验",sample_application_state_unverified:"申请状态尚未确认，留待处理",sample_payment_requires_handling:"样品费用尚未确认为免费，留待处理",sample_review_rejected:"平台未通过原审批，留待处理",sample_not_recommended_by_first_outreach:"不属于本功能成功推荐，留待处理",sample_current_offer_ineligible:"当前商品或样品额度不满足规则，留待处理",sample_current_category_mismatch:"达人当前主营类目已不匹配，留待处理",sample_current_profile_unavailable:"达人当前画像尚未确认，留待处理",sample_relationship_requires_review:"当前合作关系需要人工处理",waiting_window:"等待发送时间窗口",
 sample_facts_unavailable:"尚未取得免费样品商品与额度",sample_products_unavailable:"尚未取得免费样品商品与额度",sample_inventory_unavailable:"尚未取得免费样品额度",sample_applications_unavailable:"样品申请暂未同步",sample_approval_unverified:"巴西样品审批能力尚未验证",approval_capability_unverified:"巴西样品审批能力尚未验证",no_sample_products:"暂无有效且有额度的免费样品商品",no_matched_creators:"暂无符合主营类目与冷却规则的达人",no_candidates:"暂无符合规则的候选达人",first_send_requires_page_start:"等待在页面启动首条验证",canary_required:"请先完成首条验证",unknown_pending:"有结果未知的原操作，等待核验",approval_disabled:"自动审批未开启",approvals_disabled:"自动审批未开启",not_recommended:"不属于本功能成功推荐，留待处理",not_eligible:"申请时已不满足推荐规则，留待处理",insufficient_quota:"免费样品额度不足，留待处理",payment_required:"涉及机构付款，留待处理",pending:"待处理",needs_review:"待处理",approved:"已通过",eligible:"符合规则",in_progress:"处理中",confirmed:"已确认",sent:"推送成功",approved_confirmed:"已确认通过",review:"待处理",approval_unknown:"审批结果未知，等待核验",send_unknown:"推送结果未知，等待核验",
};
export const firstOutreachLabel=(code:string)=>{
 const isolated=/^first_model_batches_isolated:(\d{1,8})$/.exec(code);
 if(isolated)return `${Number(isolated[1])} 批模型准备暂未完成，其他商品继续，下轮自动复查`;
 const retrying=/^first_model_batches_retrying:(\d{1,8})$/.exec(code);
 if(retrying)return `${Number(retrying[1])} 批模型准备等待原批次自动重试`;
 return labels[code]??"当前步骤尚未完成，请查看运行记录";
};
export function firstApplicationSummary(data:{applicationCoverage?:string;counts:{pendingApplications:number;needsReview:number;unknown:number}}):{value:number|string;hint:string}{
 if(data.applicationCoverage!=="complete")return {value:"—",hint:"待审批范围尚未核实"};
 return {value:data.counts.pendingApplications,hint:`已确认需人工 ${data.counts.needsReview.toLocaleString()} · 结果未知 ${data.counts.unknown.toLocaleString()}`};
}
export function firstOutreachError(code:string):string{
 if(/conflict/.test(code))return "状态已在另一窗口变化，请按最新状态重新操作。";
 if(code==="invalid_first_outreach_request"||code==="invalid_query"||code==="market_mismatch")return "请求不符合巴西一发规则，请刷新后重试。";
 if(code==="local_origin_required")return "请从本机页面操作。";
 if(/canary/.test(code))return "请先完成首条验证；确认成功后才能持续推送。";
 if(/not_ready|pending|unknown/.test(code))return "当前还有等待项，请查看运行状态和等待原因。";
 return "暂时无法读取或执行一发功能，请检查运行状态后重试。";
}
export function firstOutreachCanContinue(data:{control:{mode:string};runtime:{state:string};counts:{sent:number;unknown:number}}):boolean{
 return data.control.mode!=="continuous"&&data.counts.sent>0&&["canary_confirmed","canary_complete","canary_completed","stopped","idle","ready"].includes(data.runtime.state);
}

export function firstOutreachActions(data:Parameters<typeof firstOutreachCanContinue>[0]):("prepare"|"start"|"continue"|"stop"|"reconcile")[]{
 const actions:("prepare"|"start"|"continue"|"stop"|"reconcile")[]=[];
 const stopped=data.control.mode==="stopped";
 if(stopped)actions.push("prepare");
 if(firstOutreachCanContinue(data))actions.push("continue");
 else if(stopped)actions.push("start");
 if(!stopped)actions.push("stop");
 if(data.counts.unknown>0)actions.push("reconcile");
 return actions;
}

// These are display groups for the rows returned by the API, never eligibility or approval rules.
// Unrecognised lifecycle states remain visible in the group that needs a closer look.
const finishedApplications=new Set(["completed","cancelled","closed_externally"]);
const waitingApplications=new Set(["approved","approved_confirmed","seller_pending","ready_to_ship","shipped","content_pending","in_progress"]);
const unknownApplications=new Set(["unknown","approval_unknown","send_unknown","waiting_reconciliation","quarantined_unknown","inflight","accepted","goal_observed"]);
export function firstApplicationGroups<T extends {state:string}>(applications:T[]):{attention:T[];waiting:T[];finished:T[]}{
 const groups:{attention:T[];waiting:T[];finished:T[]}={attention:[],waiting:[],finished:[]};
 for(const row of applications){
  if(finishedApplications.has(row.state))groups.finished.push(row);
  else if(waitingApplications.has(row.state))groups.waiting.push(row);
  else groups.attention.push(row);
 }
 const priority=(row:T)=>unknownApplications.has(row.state)?0:row.state==="sample_application_state_unverified"?2:1;
 groups.attention.sort((a,b)=>priority(a)-priority(b));
 return groups;
}

export function firstOutreachWaitingReasons(data:{runtime:{state:string;reason:string|null};readiness:{blockers:string[]}}):string[]{
 const state=firstOutreachLabel(data.runtime.state);
 return Array.from(new Set([...(data.runtime.reason?[data.runtime.reason]:[]),...data.readiness.blockers].map(firstOutreachLabel))).filter(reason=>reason!==state);
}

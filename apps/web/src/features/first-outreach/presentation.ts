const labels:Record<string,string>={
 stopped:"已停止",off:"尚未启动",starting:"正在启动",preparing:"正在准备匹配",syncing:"正在同步商品与申请",running:"正在执行",sending:"正在推送",continuous:"持续推送中",canary:"正在验证首条",canary_confirmed:"首条已确认，等待继续",canary_complete:"首条已确认，等待继续",canary_completed:"首条已确认，等待继续",
 account_busy:"等待账号空闲后继续",first_catalog_account_busy:"等待商品读取账号空闲后继续",workflow_resource_busy:"等待平台读取资源空闲后继续",workflow_stage_already_claimed:"当前步骤正在执行，等待原任务完成",sample_account_in_use:"样品账号正在使用，等待空闲后继续",first_resource_child_running:"原读取进程仍在执行，等待完成",ProfileBusyError:"账号正在使用，等待空闲后继续",live_guard_busy:"账号正在使用，等待空闲后继续",delivery_executor_busy:"原推送正在处理，等待结果",local_store_busy:"本机状态正在更新，稍后继续",outside_send_window:"等待发送时间窗口",seller_pending:"等待商家确认",closed_externally:"申请已在平台关闭",sample_application_coverage_unverified:"待审批范围尚未核实",quarantined_unknown:"原操作结果仍未知，已隔离并留待处理",
 waiting_reconciliation:"结果未知，等待核验",unknown:"结果未知，等待核验",waiting_pool:"等待可推荐达人",waiting_candidates:"等待可推荐达人",waiting_products:"等待可推荐商品",waiting_account:"等待账号空闲",waiting_capacity:"等待平台额度",waiting_cooldown:"等待72小时冷却结束",waiting_authorization:"等待页面启动",not_started:"等待页面启动",ready:"可以开始首条验证",idle:"等待下一步",attention:"需要处理",failed:"执行失败",blocked:"暂未就绪",
 first_outreach_prepare_required:"请先准备商品与达人匹配",first_outreach_catalog_changed:"商品或样品信息已变化，请重新准备匹配",first_outreach_matches_unavailable:"匹配数据暂不可用，请重新准备匹配",first_outreach_result_unknown:"有结果未知的原操作，等待核验",
 first_outreach_materials_unavailable:"匹配商品的推荐链接或葡语商品名尚未就绪",first_outreach_send_capability_unverified:"巴西发送账号的发送能力尚未验证",first_outreach_worker_unavailable:"执行进程未运行，请重试原操作以继续",first_outreach_job_pending:"正在执行准备或同步任务，请等待完成",first_outreach_job_stopped:"准备或同步已停止，可重新发起",first_category_taxonomy_unavailable:"尚未取得可用的商品类目词表",first_category_incomplete:"部分商品的类目尚未确认",first_category_model_unavailable:"商品类目判断未完成，请检查模型运行状态",sample_review_contract_unverified:"样品审批能力尚未验证，申请保留待处理",sample_application_assessment_incomplete:"已展示申请完成资格核对，其他申请资格待核对",sample_review_result_unknown:"原审批结果未知，等待核验",sample_application_state_unverified:"申请状态尚未确认，留待处理",sample_payment_requires_handling:"样品费用尚未确认为免费，留待处理",sample_review_rejected:"平台未通过原审批，留待处理",sample_not_recommended_by_first_outreach:"不属于本功能成功推荐，留待处理",sample_current_offer_ineligible:"当前商品或样品额度不满足规则，留待处理",sample_current_category_mismatch:"达人当前主营类目已不匹配，留待处理",sample_current_profile_unavailable:"达人当前画像尚未确认，留待处理",sample_relationship_requires_review:"当前合作关系需要人工处理",waiting_window:"等待发送时间窗口",
 sample_facts_unavailable:"尚未取得免费样品商品与额度",sample_products_unavailable:"尚未取得免费样品商品与额度",sample_inventory_unavailable:"尚未取得免费样品额度",sample_applications_unavailable:"样品申请暂未同步",sample_approval_unverified:"巴西样品审批能力尚未验证",approval_capability_unverified:"巴西样品审批能力尚未验证",no_sample_products:"暂无有效且有额度的免费样品商品",no_matched_creators:"暂无符合主营类目与冷却规则的达人",no_candidates:"暂无符合规则的候选达人",first_send_requires_page_start:"等待在页面启动首条验证",canary_required:"请先完成首条验证",unknown_pending:"有结果未知的原操作，等待核验",approval_disabled:"自动审批未开启",approvals_disabled:"自动审批未开启",not_recommended:"不属于本功能成功推荐，留待处理",not_eligible:"申请时已不满足推荐规则，留待处理",insufficient_quota:"免费样品额度不足，留待处理",payment_required:"涉及机构付款，留待处理",pending:"待处理",needs_review:"待处理",approved:"已通过",eligible:"符合规则",in_progress:"处理中",confirmed:"已确认",sent:"推送成功",approved_confirmed:"已确认通过",review:"待处理",approval_unknown:"审批结果未知，等待核验",send_unknown:"推送结果未知，等待核验",
};
export const firstOutreachLabel=(code:string)=>labels[code]??"当前步骤尚未完成，请查看运行记录";
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

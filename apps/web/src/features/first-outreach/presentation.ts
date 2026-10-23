const labels:Record<string,string>={
 stopped:"已停止",off:"尚未启动",starting:"正在启动",preparing:"正在准备匹配",syncing:"正在同步申请",running:"正在执行",sending:"正在推送",continuous:"持续推送中",canary:"正在验证首条",canary_confirmed:"首条已确认，等待继续",canary_complete:"首条已确认，等待继续",canary_completed:"首条已确认，等待继续",
 waiting_reconciliation:"结果未知，等待核验",unknown:"结果未知，等待核验",waiting_pool:"等待可推荐达人",waiting_candidates:"等待可推荐达人",waiting_products:"等待可推荐商品",waiting_account:"等待账号空闲",waiting_capacity:"等待平台额度",waiting_cooldown:"等待72小时冷却结束",waiting_authorization:"等待页面启动",not_started:"等待页面启动",ready:"可以开始首条验证",idle:"等待下一步",attention:"需要处理",failed:"执行失败",blocked:"暂未就绪",
 first_outreach_prepare_required:"请先准备商品与达人匹配",first_outreach_catalog_changed:"商品或样品信息已变化，请重新准备匹配",first_outreach_matches_unavailable:"匹配数据暂不可用，请重新准备匹配",first_outreach_result_unknown:"有结果未知的原操作，等待核验",
 sample_facts_unavailable:"尚未取得免费样品商品与额度",sample_products_unavailable:"尚未取得免费样品商品与额度",sample_inventory_unavailable:"尚未取得免费样品额度",sample_applications_unavailable:"样品申请暂未同步",sample_approval_unverified:"巴西样品审批能力尚未验证",approval_capability_unverified:"巴西样品审批能力尚未验证",no_sample_products:"暂无有效且有额度的免费样品商品",no_matched_creators:"暂无符合主营类目与冷却规则的达人",no_candidates:"暂无符合规则的候选达人",first_send_requires_page_start:"等待在页面启动首条验证",canary_required:"请先完成首条验证",unknown_pending:"有结果未知的原操作，等待核验",approval_disabled:"自动审批未开启",approvals_disabled:"自动审批未开启",not_recommended:"不属于本功能成功推荐，留待处理",not_eligible:"申请时已不满足推荐规则，留待处理",insufficient_quota:"免费样品额度不足，留待处理",payment_required:"涉及机构付款，留待处理",pending:"待处理",needs_review:"待处理",approved:"已通过",eligible:"符合规则",in_progress:"处理中",confirmed:"已确认",sent:"推送成功",approved_confirmed:"已确认通过",review:"待处理",approval_unknown:"审批结果未知，等待核验",send_unknown:"推送结果未知，等待核验",
};
export const firstOutreachLabel=(code:string)=>labels[code]??code;
export function firstOutreachError(code:string):string{
 if(/conflict/.test(code))return "状态已在另一窗口变化，请按最新状态重新操作。";
 if(code==="invalid_first_outreach_request"||code==="invalid_query"||code==="market_mismatch")return "请求不符合巴西一发规则，请刷新后重试。";
 if(code==="local_origin_required")return "请从本机页面操作。";
 if(/canary/.test(code))return "请先完成首条验证；确认成功后才能持续推送。";
 if(/not_ready|pending|unknown/.test(code))return "当前还有等待项，请查看运行状态和等待原因。";
 return "暂时无法读取或执行一发功能，请检查运行状态后重试。";
}
export function firstOutreachCanContinue(data:{control:{mode:string};runtime:{state:string};counts:{sent:number;unknown:number}}):boolean{
 return data.control.mode!=="continuous"&&data.counts.sent>0&&data.counts.unknown===0&&["canary_confirmed","canary_complete","canary_completed","stopped","idle","ready"].includes(data.runtime.state);
}

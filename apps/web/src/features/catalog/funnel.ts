/** One stage of the full-managed funnel: a labelled count that jumps to its own section. */
export type FunnelStage={key:string;label:string;value:number|null;hint:string};

/**
 * The four counts of the preparation line, derived in one place so the strip and its tests agree.
 * A missing source reports ``null`` rather than 0: "not loaded yet" and "nothing here" are
 * different facts, and showing 0 for an unread number would be a lie.
 */
export function buildFunnel(input:{collected:number|null;screened:number|null;screenedOf:number|null;linked:number|null;leadsPending:number|null}):FunnelStage[]{
 const rate=input.screened!=null&&input.screenedOf?`合格率 ${((input.screened/input.screenedOf)*100).toFixed(1)}%`:"按门槛筛选";
 return [
  {key:"card-collect",label:"采集",value:input.collected,hint:"去重商品"},
  {key:"card-screen",label:"筛出",value:input.screened,hint:rate},
  {key:"card-links",label:"标准链接",value:input.linked,hint:"当前规则已核验"},
  {key:"card-leads",label:"待查线索",value:input.leadsPending,hint:"未查过，按销量降序"},
 ];
}

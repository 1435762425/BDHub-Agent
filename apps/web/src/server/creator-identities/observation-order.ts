/** Observed time wins; a full Profile wins a tie with its Find summary.
 * rowid is the durable arrival order, unlike a random UUID event id.
 * Only trusted source code passes the alias, never request input.
 */
export function profileObservationOrder(alias="e"):string {
  if(!/^[a-z][a-z0-9_]*$/i.test(alias))throw new Error("invalid observation SQL alias");
  return `${alias}.observed_us DESC,CASE WHEN ${alias}.evidence_ref LIKE '%:profile' THEN 2 WHEN ${alias}.evidence_ref LIKE '%:find-profile' THEN 1 ELSE 0 END DESC,${alias}.rowid DESC`;
}

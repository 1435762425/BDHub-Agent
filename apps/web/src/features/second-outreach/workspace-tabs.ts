export const SECOND_OUTREACH_TABS=["send","inbox","reply","calendar","system"] as const;
export type SecondOutreachTab=(typeof SECOND_OUTREACH_TABS)[number];

export function initialSecondOutreachTab(value:string|null):SecondOutreachTab{
 return SECOND_OUTREACH_TABS.includes(value as SecondOutreachTab)?value as SecondOutreachTab:"send";
}

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildStatsCalendar,sumStatsDays} from '../src/features/second-outreach/stats-calendar.ts';

const empty=date=>({date,cards:0,texts:0,creators:0,unconfirmed:0,replies:0,showcase:0,
 ourMessages:0,autoReplies:0,casesOpened:0});

test('calendar totals are the exact sum of daily rows and unconfirmed stays separate',()=>{
 const days=[
  {...empty('2026-09-19'),cards:3,texts:3,creators:2,replies:1,unconfirmed:4},
  {...empty('2026-09-20'),cards:2,texts:1,creators:2,showcase:1,unconfirmed:1},
 ];
 assert.deepEqual(sumStatsDays(days),{cards:5,texts:4,creators:4,unconfirmed:5,replies:1,
  showcase:1,ourMessages:0,autoReplies:0,casesOpened:0});
 const model=buildStatsCalendar({days,totals:sumStatsDays(days),today:days[1]});
 assert.equal(model.days.filter(row=>row.isToday).length,1);
 assert.equal(model.days[1].isToday,true);
 assert.equal(model.days[1].dateLabel,'9月20日');
});

test('calendar refuses mismatched totals or a today row that changed underneath it',()=>{
 const days=[{...empty('2026-09-20'),cards:2,creators:1}];
 assert.throws(()=>buildStatsCalendar({days,totals:{...sumStatsDays(days),cards:3},today:days[0]}),/stats_totals_mismatch/);
 assert.throws(()=>buildStatsCalendar({days,totals:sumStatsDays(days),today:{...days[0],replies:1}}),/stats_today_mismatch/);
});

test('an empty read stays empty instead of inventing a zero-day business result',()=>{
 const model=buildStatsCalendar({days:[],totals:sumStatsDays([]),today:null});
 assert.equal(model.days.length,0);
 assert.equal(model.todayDate,null);
});

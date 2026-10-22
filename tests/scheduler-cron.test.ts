import {describe,expect,test} from 'bun:test';
import {catchUp,due,MINUTE,parseSchedule,type CronSchedule} from '../src/scheduler/cron';

const at=(text:string)=>new Date(text+'Z');
const cron=(text:string)=>{const parsed=parseSchedule(text);if(!parsed||parsed.kind!=='cron')throw new Error('not cron: '+text);return parsed as CronSchedule;};
const on=(schedule:string,moment:string)=>due(cron(schedule),at(moment));

describe('schedule syntax, as pg_cron 1.6.4 reads it', ()=>{
 test('accepts the five-field forms pg_cron accepts', ()=>{
  for(const text of ['* * * * *','*/5 * * * *','0 3 * * *','5 4 * * sun','0 0 1,15 * *','0-59/10 1-3 * * *','30 12 * jan,JUL *',
   '0 0 * * mon-fri','0 0 * * 7','0 0 $ * *','0 0 * Jan-Mar Mon','  1 2 3 4 5  ','0 0 * * */2','0 0 1-31/2 * *'])
   expect(parseSchedule(text)).not.toBeNull();
 });
 test('accepts intervals of 1 to 59 seconds in the forms TryParseInterval accepts', ()=>{
  for(const [text,seconds] of [['1 second',1],['30 seconds',30],['59 SECONDS',59],[' 2 seconds ',2],['5seconds',5],['1 seconds',1]] as const)
   expect(parseSchedule(text)).toEqual({kind:'interval',seconds});
  for(const text of ['0 seconds','60 seconds','5 secondsx','5 sec','-1 seconds','5 seconds later'])expect(parseSchedule(text)).toBeNull();
 });
 test('refuses what pg_cron refuses', ()=>{
  for(const text of ['','*','* * * *','60 * * * *','* 24 * * *','* * 0 * *','* * 32 * *','* * * 13 *','* * * * 8','1/20 * * * *',
   '5-64/30 * * * *','*/0 * * * *','* * * foo *','* * * * funday','@reboot','@restart','@never','5x * * * *','* * * * *\u0000'])
   expect(parseSchedule(text)).toBeNull();
 });
 test('refuses what pg_cron would ignore: text after the fifth field, a stray character, a $ with more', ()=>{
  for(const text of ['* * * * * extra','5; * * * *','0 0 $5 * *','0 0 1,$ * *'])expect(parseSchedule(text)).toBeNull();
 });
 test('refuses values that are not text and overlong schedules', ()=>{
  expect(parseSchedule(null)).toBeNull();expect(parseSchedule(5)).toBeNull();
  expect(parseSchedule('* * * * '+'*'.repeat(1000))).toBeNull();
 });
});

describe('when a schedule is due (ShouldRunTask, GMT)', ()=>{
 test('minute, hour and month must match', ()=>{
  expect(on('30 12 * * *','2026-10-22T12:30:00')).toBe(true);
  expect(on('30 12 * * *','2026-10-22T12:31:00')).toBe(false);
  expect(on('0 0 * jan *','2026-01-05T00:00:00')).toBe(true);
  expect(on('0 0 * jan *','2026-02-05T00:00:00')).toBe(false);
 });
 test('day of month and day of week both restricted means either one', ()=>{
  // 2026-10-22 is a Thursday.
  expect(on('0 0 1 * thu','2026-10-22T00:00:00')).toBe(true);
  expect(on('0 0 22 * mon','2026-10-22T00:00:00')).toBe(true);
  expect(on('0 0 1 * mon','2026-10-22T00:00:00')).toBe(false);
 });
 test('either day field starting with * means both must match', ()=>{
  expect(on('0 0 * * mon','2026-10-22T00:00:00')).toBe(false);
  expect(on('0 0 22 * *','2026-10-22T00:00:00')).toBe(true);
  expect(on('0 0 */2 * thu','2026-10-23T00:00:00')).toBe(false);
 });
 test('7 and 0 are both Sunday', ()=>{
  expect(on('0 0 * * 7','2026-10-25T00:00:00')).toBe(true);
  expect(on('0 0 * * 0','2026-10-25T00:00:00')).toBe(true);
 });
 test('$ is the last day of the month, leap years included', ()=>{
  expect(on('0 0 $ * *','2026-10-31T00:00:00')).toBe(true);
  expect(on('0 0 $ * *','2026-10-30T00:00:00')).toBe(false);
  expect(on('0 0 $ * *','2028-02-29T00:00:00')).toBe(true);
  expect(on('0 0 $ * *','2027-02-28T00:00:00')).toBe(true);
 });
 test('the macros', ()=>{
  expect(on('@hourly','2026-10-22T07:00:00')).toBe(true);
  expect(on('@hourly','2026-10-22T07:01:00')).toBe(false);
  expect(on('@daily','2026-10-22T00:00:00')).toBe(true);
  expect(on('@midnight','2026-10-22T01:00:00')).toBe(false);
  expect(on('@weekly','2026-10-25T00:00:00')).toBe(true);
  expect(on('@weekly','2026-10-22T00:00:00')).toBe(false);
  expect(on('@monthly','2026-11-01T00:00:00')).toBe(true);
  // pg_cron 1.6.4 never runs @yearly; here it runs on 1 January.
  expect(on('@yearly','2027-01-01T00:00:00')).toBe(true);
  expect(on('@annually','2027-02-01T00:00:00')).toBe(false);
 });
 test('wildcard and fixed-time selection, as doWild and doNonWild', ()=>{
  expect(due(cron('*/5 * * * *'),at('2026-10-22T00:05:00'),true,false)).toBe(true);
  expect(due(cron('*/5 * * * *'),at('2026-10-22T00:05:00'),false,true)).toBe(false);
  expect(due(cron('5 3 * * *'),at('2026-10-22T03:05:00'),false,true)).toBe(true);
  expect(due(cron('5 3 * * *'),at('2026-10-22T03:05:00'),true,false)).toBe(false);
  expect(due(cron('5 * * * *'),at('2026-10-22T03:05:00'),true,false)).toBe(true);
 });
});

describe('the minutes a runner looks at after its clock moved (StartPendingRuns)', ()=>{
 const base=Date.UTC(2026,9,22,12,0);
 test('within the same minute, nothing', ()=>expect(catchUp(base,base+59_000)).toEqual({minutes:[],last:base}));
 test('one to five minutes: each minute, all jobs', ()=>{
  const result=catchUp(base,base+3*MINUTE+10_000);
  expect(result.minutes).toEqual([1,2,3].map(n=>({at:base+n*MINUTE,wild:true,fixed:true})));
  expect(result.last).toBe(base+3*MINUTE);
 });
 test('six minutes to three hours: fixed-time jobs for each minute, wildcard jobs once', ()=>{
  const result=catchUp(base,base+10*MINUTE);
  expect(result.minutes.filter(m=>m.fixed).length).toBe(10);
  expect(result.minutes.filter(m=>m.wild)).toEqual([{at:base+10*MINUTE,wild:true,fixed:false}]);
 });
 test('a clock that went back keeps the last minute and runs nothing until it passes it again, as pg_cron', ()=>{
  expect(catchUp(base,base-30*MINUTE)).toEqual({minutes:[],last:base});
  expect(catchUp(base,base-500*MINUTE)).toEqual({minutes:[],last:base});
 });
 test('a jump forward of more than three hours looks at the current minute only', ()=>{
  expect(catchUp(base,base+500*MINUTE)).toEqual({minutes:[{at:base+500*MINUTE,wild:true,fixed:true}],last:base+500*MINUTE});
 });
});

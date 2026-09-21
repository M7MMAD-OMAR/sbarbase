import {test,expect} from 'bun:test';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {mailDetails,mailState,notificationText,type MailEntry} from '../ui/mail';

const ui=join(import.meta.dir,'..','ui');
const connection=readFileSync(join(ui,'Connection.tsx'),'utf8');
const app=readFileSync(join(ui,'App.tsx'),'utf8');

const applied:MailEntry={state:'applied',credentials:'set',at:1758400000,host:'smtp.example.com',port:587,
 admin_email:'noreply@example.com',sender_name:'Example',reply_to:'support@example.com',max_frequency:'60s',
 otp_exp:3600,otp_length:6,secure_email_change:true,autoconfirm:false,rate_limit_email_sent:'30',
 rate_limit_otp:30,rate_limit_verify:30,rate_limit_header:'empty'};

test('the console names each of the four mail states, and an absent entry is unconfigured',()=>{
 for(const [state,label] of [['unconfigured','Not configured'],['applied','On'],['failed','Failed'],['off','Off']] as [string,string][]) {
  const view=mailState({state});
  expect(view.state).toBe(state);expect(view.label).toBe(label);expect(view.text.length).toBeGreaterThan(0);
 }
 expect(mailState(undefined)).toEqual(mailState({state:'unconfigured'}));
 // An unknown state is not rendered as a blank or as success.
 expect(mailState({state:'whatever'}).state).toBe('unconfigured');
});

test('the email section renders the recorded facts and never a credential row',()=>{
 const rows=mailDetails(applied);
 expect(rows).toEqual([{label:'Relay',value:'smtp.example.com:587'},{label:'Sender',value:'Example <noreply@example.com>'},
  {label:'Reply to',value:'support@example.com'},{label:'Email rate limit',value:'30'},
  {label:'One time code',value:'6 digits, valid 3600 seconds'}]);
 // The summary's own `empty` marker is a marker, not a value, and a stray credential field
 // in the record is not a row this section can render.
 expect(mailDetails({state:'applied',reply_to:'empty',sender_name:'empty'})).toEqual([]);
 const stray={...applied,pass:'sup3r'};
 expect(JSON.stringify(mailDetails(stray))).not.toContain('sup3r');
 expect(mailDetails(undefined)).toEqual([]);
});

test('the console reads both new surfaces through the existing request helper',()=>{
 // The environment surface reads the mail route beside its connection and key reads.
 expect(connection).toContain("request(path+'/mail','GET'");
 expect(connection).toContain('<h2>Email</h2>');
 expect(connection).toContain("className={'state '+mailView.state}");
 // The operator sees the undelivered count in the console shell, as a live status line.
 expect(app).toContain("request('/notifications','GET'");
 expect(app).toContain('role="status"');
 expect(notificationText(3)).toBe('Undelivered notifications: 3');
 expect(notificationText(0)).toBe('Undelivered notifications: 0');
});
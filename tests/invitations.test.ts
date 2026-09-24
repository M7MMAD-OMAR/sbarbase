import {test,expect} from 'bun:test';
import {Database} from 'bun:sqlite';
import {Catalog} from '../src/control/catalog';
import {invitationHandler,type InvitationAccounts} from '../src/control/invitations';

/** A fake management realm: accounts by email, sessions by token. */
function realm() {
 const users=new Map<string,{id:string;password:string}>(),sessions=new Map<string,{id:string;email:string}>();
 const accounts:InvitationAccounts={
  async create(email,password){if(users.has(email))return 'exists';const id='u-'+email.split('@')[0];users.set(email,{id,password});return {id};},
  async session(token){return sessions.get(token)??null;}};
 return {users,sessions,accounts};
}
function setup(clock={t:1_000_000}) {
 const catalog=new Catalog(':memory:'),{users,sessions,accounts}=realm();
 const org=catalog.createOrganization('alice','A');catalog.setMember('alice',org,'bob','admin');catalog.setMember('alice',org,'carol','viewer');
 const handler=invitationHandler(catalog,async request=>request.headers.get('authorization'),accounts,()=>clock.t);
 const manage=(actor:string,method:string,body?:unknown,id='')=>handler(new Request(`http://local/management/v1/organizations/${org}/invitations${id?'/'+id:''}`,
  {method,headers:{authorization:actor,...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})}));
 const redeem=(body:unknown,session?:string)=>handler(new Request('http://local/management/invitations/redeem',
  {method:'POST',headers:{'content-type':'application/json',...(session?{authorization:'Bearer '+session}:{})},body:JSON.stringify(body)}));
 const invite=async(actor:string,email:string,role:string)=>(await (await manage(actor,'POST',{email,role})).json()).data as {id:string;token:string};
 return {catalog,org,users,sessions,manage,redeem,invite};
}

test('an owner invites, the invitee sets a password and joins, and the link works once',async()=>{
 const {catalog,org,users,manage,redeem,invite}=setup();
 const {token}=await invite('alice','Dana@Example.com','admin');
 expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
 const listed=(await (await manage('bob','GET')).json()).data;
 expect(listed).toEqual([expect.objectContaining({email:'dana@example.com',role:'admin'})]);
 expect(JSON.stringify(listed)).not.toContain(token);
 const joined=await redeem({token,password:'correct horse battery'});
 expect(joined.status).toBe(201);expect((await joined.json()).data).toEqual({organization:org,role:'admin',email:'dana@example.com'});
 expect(users.get('dana@example.com')!.password).toBe('correct horse battery');
 expect(catalog.listMembers('alice',org)).toContainEqual({actor:'u-dana',role:'admin'});
 const again=await redeem({token,password:'correct horse battery'});
 expect(again.status).toBe(400);expect((await again.json()).message).toBe('This invitation is not valid');
 const db=(catalog as unknown as {db:Database}).db;
 expect(JSON.stringify(db.query('SELECT * FROM invitations').all())).not.toContain(token);
 expect(JSON.stringify(db.query("SELECT detail FROM audit_events WHERE action LIKE 'invitation.%'").all())).not.toContain('dana');
 catalog.close();
});

test('nobody grants a role above their own; viewers and strangers cannot invite',async()=>{
 const {catalog,manage}=setup();
 expect((await manage('bob','POST',{email:'x@example.com',role:'owner'})).status).toBe(403);
 expect((await manage('bob','POST',{email:'x@example.com',role:'viewer'})).status).toBe(201);
 expect((await manage('carol','POST',{email:'x@example.com',role:'viewer'})).status).toBe(403);
 expect((await manage('mallory','GET')).status).toBe(403);
 for(const body of [{email:'not-an-email',role:'viewer'},{email:'x@example.com',role:'root'},{email:'x@example.com',role:'viewer',extra:1}])
  expect((await manage('alice','POST',body)).status).toBe(400);
 catalog.close();
});

test('unknown, cancelled and expired tokens get the same answer; a short password consumes nothing',async()=>{
 const clock={t:1_000_000};
 const {catalog,manage,redeem,invite}=setup(clock);
 const cancelled=await invite('alice','c@example.com','viewer');
 expect((await manage('alice','DELETE',undefined,cancelled.id)).status).toBe(200);
 const expiring=await invite('alice','e@example.com','viewer');
 const short=await redeem({token:expiring.token,password:'short'});
 expect(short.status).toBe(400);expect((await short.json()).message).toContain('at least 12');
 clock.t+=8*24*3600_000;
 const answers=await Promise.all([redeem({token:'A'.repeat(43),password:'long enough password'}),redeem({token:cancelled.token,password:'long enough password'}),
  redeem({token:'bad',password:'long enough password'})]);
 for(const answer of answers){expect(answer.status).toBe(400);expect(await answer.json()).toEqual({message:'This invitation is not valid'});}
 catalog.close();
});

test('an existing account signs in first; only the invited email can redeem with a session',async()=>{
 const {catalog,org,users,sessions,redeem,invite}=setup();
 users.set('erin@example.com',{id:'u-erin',password:'old password here'});
 const {token}=await invite('alice','erin@example.com','viewer');
 const exists=await redeem({token,password:'another password'});
 expect(exists.status).toBe(409);
 sessions.set('s-mallory',{id:'u-mallory',email:'mallory@example.com'});sessions.set('s-erin',{id:'u-erin',email:'Erin@example.com'});
 expect((await redeem({token},'s-mallory')).status).toBe(400);
 const joined=await redeem({token},'s-erin');
 expect(joined.status).toBe(200);expect(catalog.listMembers('alice',org)).toContainEqual({actor:'u-erin',role:'viewer'});
 catalog.close();
});

test('repeated bad tokens are rate limited for the minute',async()=>{
 const clock={t:0};const {catalog,redeem}=setup(clock);
 for(let k=0;k<20;k++)expect((await redeem({token:'x'})).status).toBe(400);
 expect((await redeem({token:'x'})).status).toBe(429);
 clock.t+=60_000;expect((await redeem({token:'x'})).status).toBe(400);
 catalog.close();
});

import {describe,expect,test} from 'bun:test';
import {invitationAccounts} from '../lab/upstream-app';

// The realm's Auth serves /admin/users and /user at its own root; supabase-js adds /auth/v1,
// which only an API gateway serves. The first live invitation found every call answered 404.
function recorder(answer:(url:URL,init:RequestInit|undefined)=>Response){
 const calls:{url:URL;method:string;authorization:string|null}[]=[];
 const request=(async(input:RequestInfo|URL,init?:RequestInit)=>{
  const url=new URL(input instanceof Request?input.url:String(input));
  calls.push({url,method:init?.method??'GET',authorization:new Headers(init?.headers).get('authorization')});
  return answer(url,init);
 }) as typeof fetch;
 return {calls,request};
}

describe('invitation accounts against the realm Auth service',()=>{
 test('an account is created at /admin/users on the Auth service itself',async()=>{
  const {calls,request}=recorder(()=>Response.json({id:'u1',email:'new@example.com',app_metadata:{},user_metadata:{},aud:'',created_at:''}));
  const accounts=invitationAccounts('http://172.18.0.5:9999/','service-role',request);
  expect(await accounts.create('new@example.com','a-long-enough-password')).toEqual({id:'u1'});
  expect(calls.map(call=>`${call.method} ${call.url.pathname}`)).toEqual(['POST /admin/users']);
  expect(calls[0]!.url.host).toBe('172.18.0.5:9999');
  expect(calls[0]!.authorization).toBe('Bearer service-role');
 });
 test('a session token is read at /user on the Auth service itself',async()=>{
  const {calls,request}=recorder(()=>Response.json({id:'u2',email:'member@example.com',app_metadata:{},user_metadata:{},aud:'',created_at:''}));
  const accounts=invitationAccounts('http://172.18.0.5:9999','service-role',request);
  expect(await accounts.session('token')).toEqual({id:'u2',email:'member@example.com'});
  expect(calls.map(call=>call.url.pathname)).toEqual(['/user']);
  expect(calls[0]!.authorization).toBe('Bearer token');
 });
 test('a taken email is reported as existing, not as a failure',async()=>{
  const {request}=recorder(()=>Response.json({code:422,error_code:'email_exists',msg:'A user with this email address has already been registered'},{status:422}));
  const accounts=invitationAccounts('http://172.18.0.5:9999','service-role',request);
  expect(await accounts.create('taken@example.com','a-long-enough-password')).toBe('exists');
 });
});

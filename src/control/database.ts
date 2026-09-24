import {mkdirSync,renameSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {Catalog} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** Direct database access for one environment (docs/guides/database-access.md).
 *
 * GET  /environments/{id}/database           state and how to connect (never the password)
 * PUT  /environments/{id}/database           {"enabled": true|false}; turning it on answers a new password once
 * POST /environments/{id}/database/password  a new password, answered once; the old one stops working
 *
 * The password is written to a private file the supervisor reads when it applies the change
 * (lab/durable_runtime.py `database_turn`); the console never stores or shows it again. */

export type DatabaseConnection={host:string;port:number;user:string;database:string};

export function connection(runtime:string,env:Record<string,string|undefined>=process.env):DatabaseConnection {
 const port=Number((env.SBARBASE_DATABASE_PORT??'').trim()||6543);
 const bind=(env.SBARBASE_DATABASE_BIND??'').trim();
 return {host:bind&&bind!=='0.0.0.0'&&bind!=='::'?bind:'127.0.0.1',port:Number.isInteger(port)?port:6543,user:`${runtime}_developer`,database:runtime};
}

export function connectionString(details:DatabaseConnection,password='[YOUR-PASSWORD]') {
 return `postgresql://${details.user}:${password}@${details.host}:${details.port}/${details.database}`;
}

function savePassword(directory:string,runtime:string):string {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 const password=randomBytes(24).toString('base64url');
 mkdirSync(directory,{recursive:true,mode:0o700});
 const path=join(directory,`${runtime}.json`),pending=path+'.pending';
 writeFileSync(pending,JSON.stringify({password}),{mode:0o600});
 renameSync(pending,path);
 return password;
}

export function databaseHandler(catalog:Catalog,identify:ManagementIdentity,directory='.secrets/upstream/database') {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/database(\/password)?$/);
  if(!match)return reply(404,{message:'Unknown route'});
  const reset=!!match[2];
  if(!(reset?['POST']:['GET','PUT']).includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   const state=catalog.databaseAccess(actor,environment);
   const details=connection(state.runtime);
   const view=(extra:object={})=>({...catalog.databaseAccess(actor,environment),connection:details,url:connectionString(details),...extra});
   if(request.method==='GET')return reply(200,{data:view()});
   let enabled=true;
   if(!reset){
    let input:unknown;
    try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
    const value=(input as {enabled?:unknown}|null)?.enabled;
    if(typeof value!=='boolean'||Object.keys(input as object).length!==1)return reply(400,{message:'Send {"enabled": true} or {"enabled": false}'});
    enabled=value;
   } else if(state.desired!=='on')return reply(409,{message:'Turn database access on first'});
   if(state.state==='pending')return reply(409,{message:'Database access change in progress'});
   // A password is made only when the login is turned on or reset, and shown only in this answer.
   const password=enabled?savePassword(directory,state.runtime):undefined;
   catalog.requestDatabaseAccess(actor,environment,enabled);
   return reply(202,{data:view(password?{password,url:connectionString(details,password)}:{})});
  } catch(error) {
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready'||message==='Database access change in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}

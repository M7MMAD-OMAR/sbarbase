// Deploy Edge Functions from a Supabase project folder to one Sbarbase environment.
//
// Usage:
//   SBARBASE_EMAIL=you@example.com bun lab/functions-deploy.ts <console URL> <environment id> <functions folder> [name ...]
//
// <functions folder> is a Supabase project's `supabase/functions`: one folder per function
// with an index.ts, and `_shared` for code they share. With no names, every function is
// deployed. `verify_jwt = false` under `[functions.<name>]` in `supabase/config.toml`
// (next to the folder) is honoured, as the Supabase CLI does; the default is true.
// The operator password is read from SBARBASE_PASSWORD, or asked for, and never printed.
import {createClient} from '@supabase/supabase-js';
import {existsSync,readdirSync,readFileSync,statSync} from 'node:fs';
import {join,relative,dirname} from 'node:path';

const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const NAME=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const [consoleUrl,environment,folder,...names]=process.argv.slice(2);
if(!consoleUrl||!environment||!folder){
 console.error('usage: SBARBASE_EMAIL=... bun lab/functions-deploy.ts <console URL> <environment id> <functions folder> [name ...]');
 process.exit(2);
}

function collect(directory:string):Record<string,string> {
 const found:Record<string,string>={};
 const walk=(current:string)=>{
  for(const entry of readdirSync(current,{withFileTypes:true})){
   if(entry.name.startsWith('.')||entry.name==='node_modules')continue;
   const path=join(current,entry.name);
   if(entry.isDirectory())walk(path);
   else if(entry.isFile()){
    if(statSync(path).size>2*1024*1024)throw new Error(`${relative(folder!,path)} is larger than 2 MiB`);
    found[relative(directory,path).split('\\').join('/')]=readFileSync(path,'utf8');
   }
  }
 };
 walk(directory);
 return found;
}

/** `verify_jwt` for each function from supabase/config.toml, when it says false. */
function verifyJwt(name:string):boolean {
 const config=join(dirname(folder!),'config.toml');
 if(!existsSync(config))return true;
 const section=readFileSync(config,'utf8').split(/^\[/m).find(part=>part.startsWith(`functions.${name}]`)||part.startsWith(`functions."${name}"]`));
 const value=section?.match(/^\s*verify_jwt\s*=\s*(true|false)/m)?.[1];
 return value!=='false';
}

async function password():Promise<string> {
 if(process.env.SBARBASE_PASSWORD)return process.env.SBARBASE_PASSWORD;
 process.stdout.write('Operator password: ');
 for await(const line of console)return line.trim();
 return '';
}

const email=process.env.SBARBASE_EMAIL;
if(!email){console.error('Set SBARBASE_EMAIL to your operator email.');process.exit(2);}
const base=consoleUrl.replace(/\/+$/,'');
const management=createClient(`${base}/management`,MANAGEMENT_KEY,{auth:{persistSession:false,autoRefreshToken:false}});
const login=await management.auth.signInWithPassword({email,password:await password()});
const token=login.data.session?.access_token;
if(!token){console.error('Sign-in failed: '+(login.error?.message??'no session'));process.exit(1);}

const available=readdirSync(folder,{withFileTypes:true}).filter(entry=>entry.isDirectory()&&!entry.name.startsWith('_')&&!entry.name.startsWith('.'))
 .map(entry=>entry.name).filter(name=>NAME.test(name));
const chosen=names.length?names:available;
const unknown=chosen.filter(name=>!available.includes(name));
if(unknown.length){console.error(`No such function folder: ${unknown.join(', ')}`);process.exit(1);}
const shared=existsSync(join(folder,'_shared'))?collect(join(folder,'_shared')):{};
let failed=0;
for(const name of chosen){
 const response=await fetch(`${base}/management/v1/environments/${environment}/functions/${name}`,{method:'PUT',
  headers:{authorization:`Bearer ${token}`,'content-type':'application/json'},
  body:JSON.stringify({files:collect(join(folder,name)),shared,verify_jwt:verifyJwt(name)})});
 const answer=await response.json().catch(()=>({})) as {message?:string;data?:{deployed?:{version:string}}};
 if(response.status===201)console.log(`deployed ${name} (${answer.data?.deployed?.version})`);
 else{failed++;console.error(`${name}: ${response.status} ${answer.message??''}`);}
}
process.exit(failed?1:0);

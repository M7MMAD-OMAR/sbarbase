import {createHmac,randomBytes} from 'node:crypto';
import {crc32} from 'node:zlib';
import {lstatSync,mkdirSync,readFileSync,readdirSync,renameSync,statSync,writeFileSync} from 'node:fs';
import {dirname,join} from 'node:path';

/** The read side of Edge Functions (docs/guides/edge-functions.md): a function's files for the
 * console's code viewer, the project shaped ZIP, secret digests and the list of default secrets.
 * Nothing here writes a function; deploys stay in functions.ts. Every path is built from a runtime
 * id, a version folder and a function name the caller already checked, and checked again here. */

const RUNTIME=/^e_[a-f0-9]{24}$/,VERSION=/^v[a-z0-9]+-[a-f0-9]{8}$/;
const ZIP_NAME=/^[A-Za-z0-9_][A-Za-z0-9_.@+-]*(\/[A-Za-z0-9_][A-Za-z0-9_.@+-]*)*$/;
const MAX_FILES=500,MAX_DEPTH=12,INLINE_FILE=256*1024,INLINE_TOTAL=2*1024*1024;

export type FunctionFile={path:string;size:number;shared:boolean};
export type FunctionSource={files:(FunctionFile&{content:string|null})[];truncated:boolean};

type Found=FunctionFile&{full:string;archive:string};

function walk(folder:string,prefix:string,out:{rel:string;full:string;size:number}[],depth=0) {
 if(depth>MAX_DEPTH||out.length>=MAX_FILES)return;
 let entries;
 try{entries=readdirSync(folder,{withFileTypes:true});}catch{return;}
 for(const entry of entries.sort((a,b)=>a.name.localeCompare(b.name))){
  const full=join(folder,entry.name),rel=prefix?`${prefix}/${entry.name}`:entry.name;
  // A symlink or a special file is never followed: a function's folder holds only what a deploy wrote.
  if(entry.isDirectory())walk(full,rel,out,depth+1);
  else if(entry.isFile()&&out.length<MAX_FILES){const stat=lstatSync(full);if(stat.isFile())out.push({rel,full,size:stat.size});}
 }
}

/** The files of one deployed version: the function's own, then `_shared`. `shared` ones are named
 * `../_shared/x` in the console, as the Supabase editor shows them, and `_shared/x` in the archive. */
function found(codeRoot:string,runtime:string,version:string,name:string):Found[] {
 if(!RUNTIME.test(runtime)||!VERSION.test(version)||!ZIP_NAME.test(name))throw new Error('Invalid function');
 const base=join(codeRoot,runtime,version);
 const own:{rel:string;full:string;size:number}[]=[],shared:{rel:string;full:string;size:number}[]=[];
 walk(join(base,name),'',own);
 walk(join(base,'_shared'),'',shared);
 const entry=(file:{rel:string;full:string;size:number},isShared:boolean):Found=>({path:isShared?`../_shared/${file.rel}`:file.rel,
  archive:isShared?`_shared/${file.rel}`:`${name}/${file.rel}`,size:file.size,shared:isShared,full:file.full});
 const order=(a:Found,b:Found)=>Number(b.path==='index.ts')-Number(a.path==='index.ts')||a.path.localeCompare(b.path);
 return [...own.map(file=>entry(file,false)).sort(order),...shared.map(file=>entry(file,true)).sort(order)].slice(0,MAX_FILES);
}

export function functionFiles(codeRoot:string,runtime:string,version:string,name:string):FunctionFile[] {
 return found(codeRoot,runtime,version,name).map(({path,size,shared})=>({path,size,shared}));
}

/** The file list with contents for the viewer. A file over 256 KiB, or one past 2 MiB in all, comes
 * without its content and the answer says it was cut; Download has everything. */
export function functionSource(codeRoot:string,runtime:string,version:string,name:string):FunctionSource {
 let used=0,truncated=false;
 const files=found(codeRoot,runtime,version,name).map(file=>{
  let content:string|null=null;
  if(file.size<=INLINE_FILE&&used+file.size<=INLINE_TOTAL){content=readFileSync(file.full,'utf8');used+=file.size;}
  else truncated=true;
  return {path:file.path,size:file.size,shared:file.shared,content};
 });
 return {files,truncated};
}

/** Little endian fields of a stored (uncompressed) ZIP. */
function u16(value:number){const b=Buffer.alloc(2);b.writeUInt16LE(value);return b;}
function u32(value:number){const b=Buffer.alloc(4);b.writeUInt32LE(value>>>0);return b;}
function dos(time:number):{date:number;clock:number} {
 const d=new Date(Math.max(time,Date.UTC(1980,0,1)));
 return {date:((d.getUTCFullYear()-1980)<<9)|((d.getUTCMonth()+1)<<5)|d.getUTCDate(),
  clock:(d.getUTCHours()<<11)|(d.getUTCMinutes()<<5)|(d.getUTCSeconds()>>1)};
}

/** A ZIP of `entries`, stored without compression. A name that could escape the folder it is
 * extracted into (a `..` part, a leading slash, a backslash) is refused, never written. */
export function zip(entries:{name:string;data:Uint8Array}[],time:number):Buffer {
 const {date,clock}=dos(time);
 const local:Buffer[]=[],central:Buffer[]=[];
 let offset=0;
 for(const entry of entries){
  if(!ZIP_NAME.test(entry.name)||entry.name.split('/').some(part=>part==='.'||part==='..')||entry.name.length>300)throw new Error('Invalid archive name');
  const name=Buffer.from(entry.name,'utf8'),data=Buffer.from(entry.data),crc=crc32(data);
  const header=Buffer.concat([u32(0x04034b50),u16(20),u16(0x0800),u16(0),u16(clock),u16(date),u32(crc),u32(data.length),u32(data.length),u16(name.length),u16(0),name]);
  local.push(header,data);
  central.push(Buffer.concat([u32(0x02014b50),u16(20),u16(20),u16(0x0800),u16(0),u16(clock),u16(date),u32(crc),u32(data.length),u32(data.length),
   u16(name.length),u16(0),u16(0),u16(0),u16(0),u32(0),u32(offset),name]));
  offset+=header.length+data.length;
 }
 const directory=Buffer.concat(central);
 return Buffer.concat([...local,directory,u32(0x06054b50),u16(0),u16(0),u16(entries.length),u16(entries.length),u32(directory.length),u32(offset),u16(0)]);
}

/** The project shaped archive of one deployed version: `<name>/index.ts`, `_shared/cors.ts`. */
export function functionArchive(codeRoot:string,runtime:string,version:string,name:string,time:number):Buffer {
 return zip(found(codeRoot,runtime,version,name).map(file=>({name:file.archive,data:readFileSync(file.full)})),time);
}

// Secret digests. The key and the update times sit beside, not inside, the folder that is mounted
// into the functions container (lab/durable_runtime.py functions_mounts), so a function never sees them.
export function digestFolder(secretsRoot:string){return join(dirname(secretsRoot),'function-digests');}

function privateWrite(path:string,content:string) {
 mkdirSync(dirname(path),{recursive:true,mode:0o700});
 const pending=`${path}.${randomBytes(4).toString('hex')}.pending`;
 writeFileSync(pending,content,{mode:0o600});
 renameSync(pending,path);
}

function digestKey(secretsRoot:string,runtime:string):Buffer {
 if(!RUNTIME.test(runtime))throw new Error('Invalid runtime');
 const path=join(digestFolder(secretsRoot),`${runtime}.key`);
 try{const text=readFileSync(path,'utf8').trim();if(/^[a-f0-9]{64}$/.test(text))return Buffer.from(text,'hex');}catch{}
 const key=randomBytes(32);
 privateWrite(path,key.toString('hex')+'\n');
 return key;
}

/** HMAC-SHA256 with this environment's own key over the name and the value. A plain hash of a short
 * secret can be guessed offline; this cannot without the key, and the same value in two environments
 * gives two digests. */
export function secretDigest(secretsRoot:string,runtime:string,name:string,value:string):string {
 return createHmac('sha256',digestKey(secretsRoot,runtime)).update(name).update('\0').update(value).digest('hex');
}

function readTimes(secretsRoot:string,runtime:string):Record<string,number> {
 try{
  const value=JSON.parse(readFileSync(join(digestFolder(secretsRoot),`${runtime}.json`),'utf8')) as {updated?:Record<string,unknown>};
  return Object.fromEntries(Object.entries(value.updated??{}).filter(([,time])=>typeof time==='number')) as Record<string,number>;
 }catch{return {};}
}

/** Remembers when each secret was last set. Called with the names a request changed. */
export function noteSecretTimes(secretsRoot:string,runtime:string,changes:Record<string,string|null>,now:number) {
 if(!RUNTIME.test(runtime))throw new Error('Invalid runtime');
 const times=readTimes(secretsRoot,runtime);
 for(const [name,value] of Object.entries(changes)){if(value===null)delete times[name];else times[name]=now;}
 privateWrite(join(digestFolder(secretsRoot),`${runtime}.json`),JSON.stringify({updated:times}));
}

export type SecretRow={name:string;digest:string;updated_at:number|null};
export function secretRows(secretsRoot:string,runtime:string,values:Record<string,string>):SecretRow[] {
 const times=readTimes(secretsRoot,runtime);
 return Object.keys(values).sort().map(name=>({name,digest:secretDigest(secretsRoot,runtime,name,values[name]!),updated_at:times[name]??null}));
}

/** What a function can read without any secret of its own, as the cloud lists them. `provided` says
 * whether this runtime sets it: yes, no, or unknown where it belongs to the pinned edge-runtime and
 * has not been checked here. */
export const DEFAULT_SECRETS:{name:string;description:string;provided:'yes'|'no'|'unknown';deprecated?:boolean}[]=[
 {name:'SUPABASE_URL',description:'The API gateway of this environment, as seen from inside a function.',provided:'yes'},
 {name:'SUPABASE_ANON_KEY',description:'A token for the anon role. Row level security still applies.',provided:'yes'},
 {name:'SUPABASE_SERVICE_ROLE_KEY',description:'A token for the service_role role. It bypasses row level security: never send it to a browser.',provided:'yes'},
 {name:'SUPABASE_DB_URL',description:'The direct PostgreSQL connection URL. Not provided here: reach the database through SUPABASE_URL.',provided:'no'},
 {name:'SUPABASE_PUBLISHABLE_KEYS',description:'JSON dictionary of publishable API keys. Not provided here.',provided:'no'},
 {name:'SUPABASE_SECRET_KEYS',description:'JSON dictionary of secret API keys. Not provided here.',provided:'no'},
 {name:'SUPABASE_JWKS',description:'The key set that verifies tokens of this environment. Not provided here.',provided:'no'},
 {name:'SB_REGION',description:'The region the function was invoked in. Set by the edge runtime.',provided:'unknown'},
 {name:'SB_EXECUTION_ID',description:'A unique identifier for each function instance. Set by the edge runtime.',provided:'unknown'},
 {name:'DENO_DEPLOYMENT_ID',description:'The version of the function code. Set by the edge runtime.',provided:'unknown'},
];

/** Which function names a runtime has deployed, from its manifest, reread only when the file changes.
 * The gateway counts a call for a function only when its name is here, so a caller who sends any name
 * cannot fill the counters. */
export function functionNames(codeRoot:string) {
 const cache=new Map<string,{stamp:string;names:Set<string>}>();
 return (runtime:string,name:string):boolean=>{
  if(!RUNTIME.test(runtime))return false;
  const path=join(codeRoot,runtime,'functions.json');
  let stamp:string;
  try{const stat=statSync(path);stamp=`${stat.mtimeMs}:${stat.size}:${stat.ino}`;}catch{cache.delete(runtime);return false;}
  let entry=cache.get(runtime);
  if(!entry||entry.stamp!==stamp){
   let names=new Set<string>();
   try{const value=JSON.parse(readFileSync(path,'utf8')) as {functions?:Record<string,unknown>};names=new Set(Object.keys(value.functions??{}));}catch{}
   entry={stamp,names};cache.set(runtime,entry);
  }
  return entry.names.has(name);
 };
}

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** A function's path is chosen by whoever calls it, and a webhook often carries its secret in it. A part that looks like a token (24 or
 * more letters, digits and - _ . + : %, other than a UUID) or like `name=value` is replaced; the rest, such as `/orders/17`, stays readable. */
export function redactCallPath(path:string):string {
 return path.split('/').map(part=>part&&!UUID.test(part)&&(/^[A-Za-z0-9_.+:%=-]{24,}$/.test(part)||/^[^=]{1,40}(?:=|%3d).+/i.test(part)||/^eyJ/.test(part))?'[redacted]':part).join('/');
}

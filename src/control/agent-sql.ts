import {SQL} from 'bun';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {connection} from './database';

/** SQL for one environment, for the Supabase-compatible API (`POST /v1/projects/{ref}/database/query`
 * and `/database/migrations`, docs/guides/agents.md).
 *
 * It connects as the environment's developer login, through the same loopback listener a person
 * uses (src/http/database-proxy.ts), with the password the console saved when database access
 * was turned on. So it can do exactly what that login can, in that one database, and nothing
 * more: no superuser, no other environment.
 *
 * Read only means read only on the server, whatever the caller asks: the query runs as one
 * prepared statement (several statements in one string are refused by PostgreSQL itself, so a
 * `COMMIT; ...` cannot end the transaction) inside a READ ONLY transaction that is always rolled
 * back. A writable query may hold several statements and answers the rows of the last one, as
 * Supabase's endpoint does. Each call opens one connection and closes it. */

export class SqlRefusal extends Error {constructor(message:string,readonly status=400){super(message);}}
/** The environment, and the listener address the console reports for it. */
export type SqlTarget={runtime:string;host?:string;port?:number};
export type SqlRunner={
 query(target:SqlTarget,input:{query:string;parameters?:unknown[];readOnly:boolean}):Promise<unknown[]>;
 migrations(target:SqlTarget):Promise<{version:string;name:string|null}[]>;
 migrate(target:SqlTarget,input:{name:string;query:string}):Promise<{version:string}>;
};

export const MAX_QUERY=1024*1024,MAX_RESULT=5*1024*1024;
const STATEMENT_TIMEOUT='120s',DEADLINE_MS=130_000;

/** PostgREST reads the schema once and keeps it; a table an agent just made would answer 404 to
 * the app until something tells it to read again. Supabase's database does this with an event
 * trigger; here the runner says so after anything that may have changed the schema. */
async function reloadSchema(sql:SQL) {
 try{await sql.unsafe("NOTIFY pgrst, 'reload schema'");}catch{}
}

function password(directory:string,runtime:string):string {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new SqlRefusal('Invalid environment');
 try {
  const value=JSON.parse(readFileSync(join(directory,`${runtime}.json`),'utf8')).password;
  if(typeof value==='string'&&/^[A-Za-z0-9_-]{24,128}$/.test(value))return value;
 } catch {}
 throw new SqlRefusal('Database access has no saved password; turn it off and on again in the console',409);
}

/** The PostgreSQL error message, which names the problem (a syntax error, a missing table), and
 * never the connection details. */
function failure(error:unknown):never {
 if(error instanceof SqlRefusal)throw error;
 const value=error as {message?:string;code?:string;errno?:string};
 const text=typeof value?.message==='string'?value.message.slice(0,2000):'The query failed';
 // PostgreSQL's own errors carry ERR_POSTGRES_SERVER_ERROR; a connection that failed or closed
 // carries another code and is not the query's fault.
 if(value.code!=='ERR_POSTGRES_SERVER_ERROR'&&/ECONNREFUSED|ENOTFOUND|CONNECTION|TIMEOUT|CLOSED/i.test(String(value.code??'')+' '+String(value.errno??'')))
  throw new SqlRefusal('The database is not reachable right now; try again shortly',503);
 if(/schema "supabase_migrations" does not exist/.test(text))
  throw new SqlRefusal('Migration history needs this environment\'s database access turned off and on once, to add its supabase_migrations schema',409);
 throw new SqlRefusal(text,400);
}

/** PostgreSQL's int8 (counts, sizes, ids) as a JSON number when it fits, as Supabase's endpoint
 * answers it, and as a string when it does not; other values as Bun reads them. */
function plain(value:unknown):unknown {
 if(typeof value==='bigint')return value<=BigInt(Number.MAX_SAFE_INTEGER)&&value>=BigInt(Number.MIN_SAFE_INTEGER)?Number(value):String(value);
 if(Array.isArray(value))return value.map(plain);
 return value;
}
function row(value:unknown):unknown {
 if(!value||typeof value!=='object'||Array.isArray(value))return plain(value);
 const out:Record<string,unknown>={};
 for(const [key,item] of Object.entries(value))out[key]=plain(item);
 return out;
}

function bounded(rows:unknown):unknown[] {
 const list=Array.isArray(rows)?[...rows].map(row):[];
 if(JSON.stringify(list,(_key,value)=>typeof value==='bigint'?String(value):value).length>MAX_RESULT)
  throw new SqlRefusal(`The result is larger than ${MAX_RESULT/1024/1024} MiB; select fewer rows or columns (add a LIMIT)`,413);
 return list;
}

// The schema is made by the runtime with the developer login as its owner
// (lab/durable_runtime.py developer_grants_sql); the login cannot create schemas itself.
const MIGRATIONS_TABLE=`CREATE TABLE IF NOT EXISTS supabase_migrations.schema_migrations(version text NOT NULL PRIMARY KEY, statements text[], name text);`;

export function developerSql(directory='.secrets/upstream/database',env:Record<string,string|undefined>=process.env,deadlineMs=DEADLINE_MS):SqlRunner {
 const open=(target:SqlTarget)=>{
  const details={...connection(target.runtime,env),...(target.host&&target.port?{host:target.host,port:target.port}:{})};
  return new SQL({hostname:details.host.replace(/^\[(.*)\]$/,'$1'),port:details.port,username:details.user,password:password(directory,target.runtime),
   database:details.database,max:1,idleTimeout:5,connectionTimeout:10,tls:false,prepare:false,bigint:true} as ConstructorParameters<typeof SQL>[0]);
 };
 async function session<T>(target:SqlTarget,work:(sql:SQL)=>Promise<T>):Promise<T> {
  const sql=open(target);
  let timer:ReturnType<typeof setTimeout>|undefined;
  try {
   await sql.unsafe(`SET statement_timeout = '${STATEMENT_TIMEOUT}'`);
   const pid=Number((await sql`SELECT pg_backend_pid() AS pid`)[0]?.pid);
   // A writable query may lift its own statement_timeout; the deadline cancels it from a second
   // connection of the same login, which may cancel its own backends.
   const deadline=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{
    // At the login's connection limit the first try can fail; it is tried again once.
    const cancel=async(attempt:number):Promise<void>=>{
     const canceller=open(target);
     try{await canceller`SELECT pg_cancel_backend(${pid})`;}
     catch{if(attempt<2){await Bun.sleep(2000);return cancel(attempt+1);}console.error(`agent SQL: could not cancel backend ${pid} of ${target.runtime}`);}
     finally{await canceller.close({timeout:2}).catch(()=>{});}
    };
    void cancel(1);
    reject(new SqlRefusal(`The query ran longer than ${Math.round(deadlineMs/1000)} seconds and was cancelled`,408));
   },deadlineMs);});
   return await Promise.race([work(sql),deadline]);
  } catch(error) {failure(error);}
  finally {clearTimeout(timer);await sql.close({timeout:2}).catch(()=>{});}
 }
 /** Rows of one SELECT-like statement through a cursor, a batch at a time, stopping at MAX_RESULT
  * bytes, so a huge result is refused before it is in memory. Null when the statement cannot be
  * a cursor (SHOW, EXPLAIN, a write), which then runs as it is; the caller is in a transaction. */
 async function cursor(tx:any,query:string,params:unknown[]):Promise<unknown[]|null> {
  // Only a statement that reads rows can be a cursor; anything else runs as it is.
  if(!/^\s*(\(\s*)*(select|with|values|table)\b/i.test(query.replace(/^(\s*(--[^\n]*\n|\/\*[\s\S]*?\*\/))*/,'')))return null;
  await tx`SAVEPOINT sbarbase_cursor`;
  try {
   // One prepared statement: DECLARE refuses a second one in the same text.
   if(params.length)await tx.unsafe(`DECLARE sbarbase_rows NO SCROLL CURSOR FOR ${query}`,params as any[]);
   else await tx`DECLARE sbarbase_rows NO SCROLL CURSOR FOR ${tx.unsafe(query)}`;
  } catch(error) {
   const text=(error as {message?:string})?.message??'';
   await tx`ROLLBACK TO SAVEPOINT sbarbase_cursor`;
   if(/cannot open .* query as cursor|must not contain data-modifying|multiple commands into a prepared statement/i.test(text))return null;
   throw error;
  }
  const rows:unknown[]=[];let size=2;
  while(true){
   const batch=[...await tx`FETCH 500 FROM sbarbase_rows`];
   for(const item of batch){
    const value=row(item);
    size+=JSON.stringify(value).length+1;
    if(size>MAX_RESULT)throw new SqlRefusal(`The result is larger than ${MAX_RESULT/1024/1024} MiB; select fewer rows or columns (add a LIMIT)`,413);
    rows.push(value);
   }
   if(batch.length<500)break;
  }
  await tx`CLOSE sbarbase_rows`;
  return rows;
 }
 return {
  async query(target,{query,parameters,readOnly}) {
   if(typeof query!=='string'||!query.trim())throw new SqlRefusal('Send a query');
   if(query.length>MAX_QUERY)throw new SqlRefusal('The query is larger than 1 MiB');
   if(parameters!==undefined&&(!Array.isArray(parameters)||parameters.length>1000))throw new SqlRefusal('"parameters" is a list');
   const params=parameters??[];
   return session(target,async sql=>{
    if(readOnly){
     let rows:unknown;
     const rollback=new Error('rollback');
     try {
      await sql.begin('read only',async tx=>{
       // No data can change in a read-only transaction that is rolled back. What one statement can
       // still do is what the login may do outside data: cancel this environment's own sessions
       // or hold a lock until the deadline (docs/guides/agents.md, "How it is kept safe").
       rows=await cursor(tx,query,params)??(params.length?await tx.unsafe(query,params as any[]):await tx`${tx.unsafe(query)}`);
       throw rollback;
      });
     } catch(error) {if(error!==rollback)throw error;}
     return bounded(rows);
    }
    // One statement that can be a cursor is read in bounded batches; anything else runs as sent,
    // several statements included, and the last one's rows are answered.
    let rows:unknown[]|null=null;
    await sql.begin(async tx=>{rows=await cursor(tx,query,params);});
    if(rows)return rows;
    try {
     if(params.length)return bounded(await sql.unsafe(query,params as any[]));
     const results=await sql.unsafe(query).simple();
     const last=Array.isArray(results)&&results.length&&Array.isArray(results[results.length-1])?results[results.length-1]:results;
     return bounded(last);
    } finally {await reloadSchema(sql);}
   });
  },
  async migrations(target) {
   return session(target,async sql=>{
    const exists=await sql`SELECT to_regclass('supabase_migrations.schema_migrations') IS NOT NULL AS present`;
    if(!exists[0]?.present)return [];
    const rows=await sql`SELECT version, name FROM supabase_migrations.schema_migrations ORDER BY version`;
    return [...rows].map((row:any)=>({version:String(row.version),name:row.name??null}));
   });
  },
  async migrate(target,{name,query}) {
   if(typeof name!=='string'||!/^[A-Za-z0-9_.-]{1,200}$/.test(name))throw new SqlRefusal('A migration name has letters, digits, dots, dashes and underscores');
   if(typeof query!=='string'||!query.trim())throw new SqlRefusal('Send a query');
   if(query.length>MAX_QUERY)throw new SqlRefusal('The migration is larger than 1 MiB');
   return session(target,async sql=>{
    let version=new Date().toISOString().replace(/[-:T]/g,'').slice(0,14);
    // The migration and its history row commit together, or neither does.
    await sql.begin(async tx=>{
     await tx.unsafe(MIGRATIONS_TABLE).simple();
     // A version is a UTC timestamp, as the Supabase CLI writes; two in the same second, or one
     // behind a later version already applied, take the next number so the order holds.
     const latest=await tx`SELECT max(version) AS v FROM supabase_migrations.schema_migrations WHERE version ~ '^[0-9]{14}$'`;
     const last=latest[0]?.v as string|null;
     // (A migration that ends the transaction itself, with COMMIT, is not covered by this.)
     if(last&&last>=version){
      const at=(text:string)=>Date.UTC(+text.slice(0,4),+text.slice(4,6)-1,+text.slice(6,8),+text.slice(8,10),+text.slice(10,12),+text.slice(12,14));
      version=new Date(at(last)+1000).toISOString().replace(/[-:T]/g,'').slice(0,14);
     }
     await tx.unsafe(query).simple();
     await tx`INSERT INTO supabase_migrations.schema_migrations(version, statements, name) VALUES (${version}, ARRAY[${query}::text], ${name})`;
    });
    await reloadSchema(sql);
    return {version};
   });
  },
 };
}

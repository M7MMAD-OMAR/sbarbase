import {connect,type Socket} from 'node:net';
import {createHash,createHmac,pbkdf2Sync,randomBytes,timingSafeEqual} from 'node:crypto';

/** The connection a scheduled job runs on (docs/engineering/SCHEDULED-JOBS.md): PostgreSQL's simple query
 * protocol, as pg_cron's libpq session uses it, reading every message as it streams in and keeping only the
 * last command's tag and the first error. A job's rows and notices are counted or dropped, never held, so a
 * job returning gigabytes costs its environment's scheduler process nothing; COPY is refused as pg_cron refuses
 * it. The platform's own bookkeeping uses rows(), which keeps a bounded result.
 * SCRAM-SHA-256 only, which is what the cluster's access rules ask of scoped logins; no TLS, as the database is
 * reached on the host's own Docker bridge. */

/** `tuples` when the last statement returned rows (or was empty), as libpq's PGRES_TUPLES_OK and PGRES_EMPTY_QUERY. */
export type Outcome={ok:true;tag:string;rows:number;tuples:boolean}|{ok:false;message:string};
export type Row=Record<string,string|null>;
export type Rows={ok:true;tag:string;rows:Row[]}|{ok:false;message:string};
export type WireOptions={host:string;port:number;user:string;database:string;password:string;application:string;connectMs?:number};

const MAX_MESSAGE=2*1024*1024; // Any message we keep (errors, tags, auth, kept rows); larger ones are only skipped.
// The most a kept result may hold (the platform's own bookkeeping queries, never a job's).
const MAX_KEPT=24*1024*1024;
// PBKDF2 runs on the process's own thread. PostgreSQL's default is 4096, and the platform writes the scheduler
// login's password with it (lab/scheduled_jobs.py login_sql); a job that runs RESET ROLE can rewrite that
// password with any count, so a larger one is refused before any work (about 9 ms at this cap).
export const MAX_ITERATIONS=100_000;

function message(type:string,body:Buffer){const head=Buffer.alloc(5);head.write(type,0,'latin1');head.writeInt32BE(body.length+4,1);return Buffer.concat([head,body]);}
const cstring=(text:string)=>Buffer.concat([Buffer.from(text,'utf8'),Buffer.from([0])]);

export class WireSession {
 private socket:Socket|null=null;
 private chunks:Buffer[]=[];
 private buffered=0;
 private skipping=0;
 // Bytes dropped since the last collection: socket buffers a job's rows arrived in are garbage at once, and the
 // engine collects them late, so a long result would otherwise grow the process far past what it holds.
 private dropped=0;
 private waiters:(()=>void)[]=[];
 private closed:Error|null=null;
 pid:number|null=null;
 get ended(){return this.closed!==null;}

 static async open(options:WireOptions):Promise<WireSession> {
  const session=new WireSession();
  await session.start(options);
  return session;
 }

 private async start(options:WireOptions){
  const socket=this.socket=connect({host:options.host,port:options.port});
  socket.setNoDelay(true);
  socket.on('data',(chunk:Buffer)=>this.feed(chunk));
  const fail=(error:Error)=>{this.closed??=error;this.notify();};
  socket.on('error',fail);
  socket.on('close',()=>fail(new Error('Connection closed')));
  // Destroying with an error rejects a connect still waiting, as well as anything waiting for a message.
  const timer=setTimeout(()=>{const error=new Error('Connection timed out');fail(error);socket.destroy(error);},options.connectMs??10_000);
  try {
   await new Promise<void>((done,reject)=>{socket.once('connect',done);socket.once('error',reject);});
   const parameters=Buffer.concat([cstring('user'),cstring(options.user),cstring('database'),cstring(options.database),
    cstring('application_name'),cstring(options.application),Buffer.from([0])]);
   const version=Buffer.alloc(4);version.writeInt32BE(196608);
   const length=Buffer.alloc(4);length.writeInt32BE(8+parameters.length);
   socket.write(Buffer.concat([length,version,parameters]));
   await this.authenticate(options.user,options.password);
   for(;;){
    const [type,body]=await this.next();
    if(type==='K'&&body)this.pid=body.readInt32BE(0);
    else if(type==='E')throw new Error(errorText(body));
    else if(type==='Z')break;
   }
  } catch(error) {
   this.close();
   throw error;
  } finally {clearTimeout(timer);}
 }

 private async authenticate(user:string,password:string){
  void user;
  let nonce='',first='',serverSignature:Buffer|null=null;
  for(;;){
   const [type,body]=await this.next();
   if(type==='E')throw new Error(errorText(body));
   if(type!=='R'||!body)throw new Error('Unexpected message during authentication');
   const code=body.readInt32BE(0);
   if(code===0)return;
   if(code===10){
    if(!body.subarray(4).toString('utf8').split('\0').includes('SCRAM-SHA-256'))throw new Error('The server does not offer SCRAM-SHA-256');
    nonce=randomBytes(18).toString('base64');
    first=`n=,r=${nonce}`;
    const initial=Buffer.from('n,,'+first,'utf8');
    const length=Buffer.alloc(4);length.writeInt32BE(initial.length);
    this.socket!.write(message('p',Buffer.concat([cstring('SCRAM-SHA-256'),length,initial])));
   } else if(code===11){
    const server=body.subarray(4).toString('utf8');
    const fields=Object.fromEntries(server.split(',').map(part=>[part[0],part.slice(2)]));
    if(!fields.r?.startsWith(nonce)||!fields.s||!/^[0-9]+$/.test(fields.i??''))throw new Error('Invalid SCRAM challenge');
    const iterations=Number(fields.i);
    if(iterations<1||iterations>MAX_ITERATIONS)throw new Error('The scheduler login\'s password was changed (SCRAM iteration count); turn Scheduled jobs off and on, or restart Sbarbase');
    const salted=pbkdf2Sync(password.normalize('NFKC'),Buffer.from(fields.s,'base64'),iterations,32,'sha256');
    const clientKey=createHmac('sha256',salted).update('Client Key').digest();
    const stored=createHash('sha256').update(clientKey).digest();
    const withoutProof=`c=biws,r=${fields.r}`;
    const auth=`${first},${server},${withoutProof}`;
    const signature=createHmac('sha256',stored).update(auth).digest();
    const proof=Buffer.alloc(32);for(let i=0;i<32;i++)proof[i]=clientKey[i]!^signature[i]!;
    serverSignature=createHmac('sha256',createHmac('sha256',salted).update('Server Key').digest()).update(auth).digest();
    this.socket!.write(message('p',Buffer.from(`${withoutProof},p=${proof.toString('base64')}`,'utf8')));
   } else if(code===12){
    const final=body.subarray(4).toString('utf8');
    const verifier=Buffer.from(final.startsWith('v=')?final.slice(2):'','base64');
    if(!serverSignature||verifier.length!==32||!timingSafeEqual(verifier,serverSignature))throw new Error('The server could not prove it knows the password');
   } else throw new Error('Only SCRAM-SHA-256 authentication is supported');
  }
 }

 /** One simple query, which may hold several statements, as PQsendQuery. Rows are counted and dropped. */
 async query(text:string):Promise<Outcome> {
  const outcome=await this.run(text,false);
  return outcome.ok?{ok:true,tag:outcome.tag,rows:outcome.count,tuples:outcome.tuples}:outcome;
 }

 /** One simple query whose rows (of the last result) are kept, as text, for the platform's own bookkeeping: at
  * most 2 MiB a row and 24 MiB in all, beyond which the query fails. */
 async rows(text:string):Promise<Rows> {
  const outcome=await this.run(text,true);
  return outcome.ok?{ok:true,tag:outcome.tag,rows:outcome.kept}:outcome;
 }

 private async run(text:string,keep:boolean):Promise<{ok:true;tag:string;count:number;tuples:boolean;kept:Row[]}|{ok:false;message:string}> {
  if(!this.socket||this.closed)return {ok:false,message:this.closed?.message??'Connection closed'};
  this.socket.write(message('Q',cstring(text)));
  try{return await this.result(keep);}
  catch(error){return {ok:false,message:(error as Error).message??'Connection closed'};}
 }

 private async result(keep:boolean):Promise<{ok:true;tag:string;count:number;tuples:boolean;kept:Row[]}|{ok:false;message:string}> {
  let tag='',rows=0,error:string|null=null,names:string[]=[],kept:Row[]=[],size=0,described=false,tuples=false;
  // A result streaming in faster than it is read would keep this loop on microtasks alone, so timers (the run's
  // own deadline among them) and other sockets would never run: the loop gives way to the event loop every 5 ms.
  let since=performance.now();
  for(;;){
   if(performance.now()-since>5){
    // Paused meanwhile, so the server waits (TCP backpressure) instead of this process buffering what it sends.
    this.socket?.pause();
    await new Promise(done=>setTimeout(done,0));
    this.socket?.resume();
    since=performance.now();
   }
   const [type,body]=await this.next(!keep);
   if(type==='D'){
    rows++;
    if(keep){
     size+=body?.length??MAX_KEPT;
     if(!body||size>MAX_KEPT)error??='The result is larger than the scheduler keeps';
     else if(error===null)kept.push(row(body,names));
    }
    continue;
   }
   if(type==='d'||type==='N'||type==='A')continue;
   if(type==='C'&&body){tag=body.subarray(0,body.indexOf(0)).toString('utf8');tuples=described;described=false;continue;}
   if(type==='I'){tag='';rows=0;tuples=true;continue;}
   if(type==='T'){rows=0;kept=[];size=0;described=true;if(keep&&body)names=columns(body);continue;}
   if(type==='E'){error??=keep?errorText(body):libpqError(body);continue;}
   if(type==='G'){this.socket?.write(message('f',cstring('COPY not supported')));error='COPY not supported';continue;}
   if(type==='H'||type==='W'){
    // pg_cron records "COPY not supported" and leaves the session; the server stops sending once it is gone.
    this.close();
    return {ok:false,message:'COPY not supported'};
   }
   if(type==='Z')return error===null?{ok:true,tag,count:rows,tuples,kept}:{ok:false,message:error};
  }
 }

 close(){if(this.socket){try{this.socket.end(message('X',Buffer.alloc(0)));}catch{}this.socket.destroy();this.socket=null;}this.closed??=new Error('Connection closed');this.notify();}

 private notify(){for(const wake of this.waiters.splice(0))wake();}

 private collect(size:number){
  this.dropped+=size;
  if(this.dropped>=32*1024*1024){this.dropped=0;Bun.gc(false);}
 }

 private feed(chunk:Buffer){
  if(this.skipping>0){
   const skip=Math.min(this.skipping,chunk.length);
   this.skipping-=skip;chunk=chunk.subarray(skip);
   this.collect(skip);
   if(!chunk.length)return;
  }
  this.chunks.push(chunk);this.buffered+=chunk.length;
  this.notify();
 }

 /** The next `size` bytes: a view of the first chunk when it holds them, otherwise a copy of only those bytes. */
 private take(size:number):Buffer|null {
  if(this.buffered<size)return null;
  const first=this.chunks[0];
  if(first&&first.length>=size){
   if(first.length===size)this.chunks.shift();else this.chunks[0]=first.subarray(size);
   this.buffered-=size;
   return first.subarray(0,size);
  }
  const out=Buffer.allocUnsafe(size);
  let filled=0;
  while(filled<size){
   const chunk=this.chunks[0]!,part=Math.min(chunk.length,size-filled);
   chunk.copy(out,filled,0,part);filled+=part;
   if(part===chunk.length)this.chunks.shift();else this.chunks[0]=chunk.subarray(part);
  }
  this.buffered-=size;
  return out;
 }

 /** Drop the next `size` bytes, of those buffered, without copying them. */
 private discard(size:number){
  let left=Math.min(size,this.buffered);
  this.buffered-=left;
  while(left>0){
   const chunk=this.chunks[0]!;
   if(chunk.length<=left){left-=chunk.length;this.chunks.shift();}
   else {this.chunks[0]=chunk.subarray(left);left=0;}
  }
 }

 /** The next message. A message too large to keep, or a row while `drop` is set, is skipped as it streams
  * in and returned with no body. */
 private async next(drop=false):Promise<[string,Buffer|null]> {
  for(;;){
   if(this.buffered>=5){
    const head=this.take(5)!;
    const type=String.fromCharCode(head[0]!),length=head.readInt32BE(1)-4;
    if(length<0)throw new Error('Malformed message');
    if((drop&&(type==='D'||type==='d'||type==='N'||type==='A'))||length>MAX_MESSAGE){
     const now=Math.min(length,this.buffered);
     this.discard(now);
     this.collect(now);
     this.skipping=length-now;
     // Back to result(), which gives way to the event loop every 5 ms whatever kind of message streams in.
     return [type,null];
    }
    while(this.buffered<length){if(this.closed)throw this.closed;await new Promise<void>(done=>this.waiters.push(done));}
    return [type,this.take(length)];
   }
   if(this.closed)throw this.closed;
   await new Promise<void>(done=>this.waiters.push(done));
  }
 }
}

function columns(body:Buffer):string[] {
 const count=body.readInt16BE(0),names:string[]=[];
 let offset=2;
 for(let i=0;i<count;i++){const end=body.indexOf(0,offset);names.push(body.subarray(offset,end).toString('utf8'));offset=end+1+18;}
 return names;
}

function row(body:Buffer,names:string[]):Row {
 const count=body.readInt16BE(0),out:Row={};
 let offset=2;
 for(let i=0;i<count;i++){
  const length=body.readInt32BE(offset);offset+=4;
  out[names[i]??String(i)]=length<0?null:body.subarray(offset,offset+length).toString('utf8');
  if(length>0)offset+=length;
 }
 return out;
}

/** An error as libpq's PQresultErrorMessage writes it at its default verbosity: severity, message, then the
 * detail and hint lines when there are any. What pg_cron records for a failed run. */
function libpqError(body:Buffer|null):string {
 if(!body)return 'The job failed';
 const fields:Record<string,string>={};
 let offset=0;
 while(offset<body.length&&body[offset]!==0){
  const end=body.indexOf(0,offset+1);
  if(end<0)break;
  fields[String.fromCharCode(body[offset]!)]=body.subarray(offset+1,end).toString('utf8');
  offset=end+1;
 }
 let text=`${fields.S??'ERROR'}:  ${fields.M??'The job failed'}\n`;
 if(fields.D)text+=`DETAIL:  ${fields.D}\n`;
 if(fields.H)text+=`HINT:  ${fields.H}\n`;
 return text.slice(0,2000);
}

function errorText(body:Buffer|null):string {
 if(!body)return 'The job failed';
 let message='',offset=0;
 while(offset<body.length&&body[offset]!==0){
  const code=String.fromCharCode(body[offset]!);
  const end=body.indexOf(0,offset+1);
  if(end<0)break;
  if(code==='M')message=body.subarray(offset+1,end).toString('utf8');
  offset=end+1;
 }
 return (message||'The job failed').slice(0,2000);
}

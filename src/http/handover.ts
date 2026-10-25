import {readFileSync,readdirSync,readlinkSync,writeFileSync,renameSync,unlinkSync} from 'node:fs';
import {join} from 'node:path';
import {isIPv4,isIPv6} from 'node:net';

/** The console process's side of the in-place handover (docs/engineering/HANDOVER.md, lab/handover.py).
 * On unless SBARBASE_HANDOVER=0: with that value nothing here runs. A candidate (SBARBASE_CANDIDATE=1) binds the
 * fixed listeners with SO_REUSEPORT, finds its own descriptor for each in /proc and attaches a one
 * instruction reuseport program to it, so every new connection of the group comes to it while the
 * previous version's process, which joined first, retires. */

export const SOL_SOCKET=1,SO_ATTACH_REUSEPORT_CBPF=51,SO_DETACH_REUSEPORT_BPF=68;
/** The candidate's exit status when a fixed listener does not bind or cannot be steered at promotion. */
export const PROMOTION_FAILED=75;
export const STATE_DIRECTORY='.lab/upstream';

export const handoverEnabled=(env:Record<string,string|undefined>=process.env)=>env.SBARBASE_HANDOVER!=='0';
export const candidateRequested=(env:Record<string,string|undefined>=process.env)=>handoverEnabled(env)&&env.SBARBASE_CANDIDATE==='1';

/** The PROTOCOL of a lab/handover.py read as text: exactly one line `PROTOCOL = <n>`. */
export function protocolOf(text:string):number {
 const found=text.match(/^PROTOCOL = (\d+)$/gm);
 if(!found||found.length!==1)throw new Error('lab/handover.py does not declare exactly one PROTOCOL line');
 return Number(found[0]!.slice('PROTOCOL = '.length));
}
export const readProtocol=(path='lab/handover.py')=>protocolOf(readFileSync(path,'utf8'));

/** A listener's bind address as /proc/net/tcp or tcp6 prints it (each 32 bit word in host byte order). */
export function procAddress(host:string):{file:'tcp'|'tcp6';hex:string} {
 if(host==='localhost')host='127.0.0.1';
 if(isIPv4(host)){
  const octets=host.split('.').map(Number);
  return {file:'tcp',hex:octets.reverse().map(octet=>octet.toString(16).padStart(2,'0')).join('').toUpperCase()};
 }
 if(!isIPv6(host))throw new Error(`${host} is not an IP address`);
 const [head,tail]=host.split('::');
 const words=(text:string|undefined)=>text?text.split(':').map(word=>parseInt(word,16)):[];
 const front=words(head),back=host.includes('::')?words(tail):[];
 const groups=host.includes('::')?[...front,...Array(8-front.length-back.length).fill(0),...back]:front;
 if(groups.length!==8)throw new Error(`${host} is not an IPv6 address`);
 const bytes=groups.flatMap(group=>[group>>8,group&255]);
 let hex='';
 for(let at=0;at<16;at+=4)hex+=bytes.slice(at,at+4).reverse().map(byte=>byte.toString(16).padStart(2,'0')).join('');
 return {file:'tcp6',hex:hex.toUpperCase()};
}

/** The inodes of every socket in state 0A (listen) at host:port. */
export function listeningInodes(host:string,port:number,proc='/proc'):number[] {
 const {file,hex}=procAddress(host);
 const found:number[]=[];
 for(const line of readFileSync(join(proc,'net',file),'utf8').split('\n').slice(1)){
  const fields=line.trim().split(/\s+/);
  if(fields.length>9&&fields[3]==='0A'&&fields[1]===`${hex}:${port.toString(16).toUpperCase().padStart(4,'0')}`)found.push(Number(fields[9]));
 }
 return found;
}

/** This process's own descriptor for its listener at host:port: the descriptor whose socket inode is
 * the `0A` entry for that address. Exactly one descriptor must match, or it throws. */
export function ownDescriptor(host:string,port:number,proc='/proc',pid='self'):number {
 const inodes=new Set(listeningInodes(host,port,proc));
 const fds:number[]=[];
 const folder=join(proc,String(pid),'fd');
 for(const name of readdirSync(folder)){
  try{
   const match=/^socket:\[(\d+)\]$/.exec(readlinkSync(join(folder,name)));
   if(match&&inodes.has(Number(match[1])))fds.push(Number(name));
  }catch{}
 }
 if(fds.length!==1)throw new Error(`${fds.length} descriptors of this process match the listener at ${host}:${port}`);
 return fds[0]!;
}

/** setsockopt on a descriptor: the program attach and detach, through bun:ffi. */
export type Steering={attach(fd:number):number;detach(fd:number):number};

/** libc's setsockopt. The attach passes struct sock_fprog {u16 len=1; pad; u64 filter} (16 bytes) with
 * the filter BPF_RET|BPF_K 1, so every new connection of the group goes to its second member; the
 * detach passes a 4 byte int 0, since the kernel refuses an option length below sizeof(int). */
export function libcSteering():Steering {
 const {dlopen,FFIType,ptr}=require('bun:ffi') as typeof import('bun:ffi');
 const libc=dlopen('libc.so.6',{setsockopt:{args:[FFIType.i32,FFIType.i32,FFIType.i32,FFIType.ptr,FFIType.u32],returns:FFIType.i32}});
 const filter=new Uint8Array(8),program=new Uint8Array(16),zero=new Int32Array(1);
 const view=new DataView(filter.buffer);view.setUint16(0,0x06,true);view.setUint32(4,1,true);
 const fprog=new DataView(program.buffer);fprog.setUint16(0,1,true);fprog.setBigUint64(8,BigInt(ptr(filter)),true);
 return {
  attach:fd=>libc.symbols.setsockopt(fd,SOL_SOCKET,SO_ATTACH_REUSEPORT_CBPF,ptr(program),16),
  detach:fd=>libc.symbols.setsockopt(fd,SOL_SOCKET,SO_DETACH_REUSEPORT_BPF,ptr(zero),4),
 };
}

export type FixedListener={name:string;host:string;port:number;steered?:boolean};

/** The listeners record `.lab/upstream/listeners-<pid>.json` the supervisor reads (lab/handover.py). */
export class ListenerRecord {
 constructor(private directory:string,private pid:number,private protocol:number,public role:'serving'|'candidate'){}
 get path(){return join(this.directory,`listeners-${this.pid}.json`);}
 write(frozen:boolean,listeners:FixedListener[]){
  const pending=this.path+'.pending';
  writeFileSync(pending,JSON.stringify({pid:this.pid,protocol:this.protocol,role:this.role,frozen,
   listeners:listeners.map(item=>({name:item.name,host:item.host,port:item.port,steered:item.steered===true}))}),{mode:0o600});
  renameSync(pending,this.path);
 }
 remove(){try{unlinkSync(this.path);}catch{}}
}

/** A process's start time in clock ticks since boot (field 22 of /proc/<pid>/stat), or undefined when it is gone. */
export function processStart(pid:number,proc='/proc'):number|undefined {
 try{
  const text=readFileSync(join(proc,String(pid),'stat'),'utf8');
  const fields=text.slice(text.lastIndexOf(')')+2).split(' ');
  const value=Number(fields[19]);
  return Number.isInteger(value)?value:undefined;
 }catch{return undefined;}
}

/** Whether `handover.json` names this process: the supervisor has decided to hand over, so this process must
 * stop opening and closing fixed listeners (the candidate's set is compared with it). The record names a pid and
 * its start time (lab/handover.py leave_for_exec); a record left by an earlier run that names a pid this process
 * happens to have now does not count, since its start time is another. */
export function namedForHandover(directory:string,pid:number,proc='/proc'):boolean {
 try{
  const record=JSON.parse(readFileSync(join(directory,'handover.json'),'utf8')) as {pid?:unknown;started?:unknown}|null;
  return record?.pid===pid&&typeof record.started==='number'&&record.started===processStart(pid,proc);
 }catch{return false;}
}

/** The pid header of `GET /health`, only for a direct loopback caller: a request that carries
 * `forwarded` or any `x-forwarded-*` header came through the TLS proxy and sees nothing. The caller adds it only
 * while a handover is pending or being coordinated (`coordinated`). */
export function withPidHeader(response:Response,request:Request,pid=process.pid):Response {
 for(const name of request.headers.keys())if(name==='forwarded'||name.startsWith('x-forwarded-'))return response;
 const headers=new Headers(response.headers);headers.set('x-sbarbase-pid',String(pid));
 return new Response(response.body,{status:response.status,headers});
}

/** Whether this process takes part in a handover now: a candidate always does (the supervisor probes it by its pid),
 * a serving process once `handover.json` names it. Any other direct request to `/health` learns nothing about the process. */
export const coordinated=(directory:string,pid:number,candidate:boolean,proc='/proc'):boolean => candidate||namedForHandover(directory,pid,proc);

/** H3: the promoted candidate's `server.json`, written only once every fixed listener is bound and
 * steered: the shared console address, this process, the protocol and the private address beside it. */
export function publishPromoted(directory:string,shared:string,privateUrl:string,protocol:number,pid=process.pid) {
 const pending=join(directory,'server.json.pending');
 writeFileSync(pending,JSON.stringify({url:shared,pid,protocol,private:privateUrl,promoted:true}));
 renameSync(pending,join(directory,'server.json'));
}

/** The candidate's signal sequence. The first SIGUSR2 promotes (binds and steers); once promoted the
 * next releases the steering; any later one is ignored. A release asked while the promotion is still
 * running waits for it. */
export function candidateSignals(actions:{promote():Promise<void>;release():void;failed(error:unknown):void}) {
 let count=0,promoted=false,releaseLater=false;
 return ():void=>{
  count++;
  if(count===1){
   void actions.promote().then(()=>{promoted=true;if(releaseLater)actions.release();},error=>actions.failed(error));
  }else if(count===2){
   if(promoted)actions.release();else releaseLater=true;
  }
 };
}

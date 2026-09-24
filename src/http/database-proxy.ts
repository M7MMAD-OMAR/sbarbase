import {connect,createServer,type Socket} from 'node:net';

/** Direct database access for one environment at a time (docs/guides/database-access.md).
 *
 * A PostgreSQL client connects here; this reads its startup message and passes the connection
 * to the shared database only when the login is an environment's developer login asking for
 * that environment's own database. Every other login, the superuser included, is refused
 * before a byte reaches PostgreSQL, which then checks the password itself. TLS is declined:
 * the listener is on loopback by default, reached over an SSH tunnel. */

const SSL_REQUEST=80877103,GSS_REQUEST=80877104,CANCEL_REQUEST=80877102,PROTOCOL_3=196608;
const MAX_STARTUP=10_000;
export const DEVELOPER_LOGIN=/^(e_[a-f0-9]{24})_developer$/;

export type StartupDecision={ok:true;user:string;database:string}|{ok:false;message:string};

/** Reads `user` and `database` from a protocol 3 startup message body (after length and version). */
export function startupFields(body:Buffer):Record<string,string> {
 const fields:Record<string,string>={};
 let offset=0;
 while(offset<body.length){
  const end=body.indexOf(0,offset);
  if(end<0||end===offset)break;
  const key=body.subarray(offset,end).toString('utf8');
  const valueEnd=body.indexOf(0,end+1);
  if(valueEnd<0)break;
  fields[key]=body.subarray(end+1,valueEnd).toString('utf8');
  offset=valueEnd+1;
 }
 return fields;
}

export function decide(fields:Record<string,string>):StartupDecision {
 const user=fields.user??'';
 const runtime=user.match(DEVELOPER_LOGIN)?.[1];
 if(!runtime)return {ok:false,message:'Only an environment developer login (e_…_developer) can connect here'};
 const database=fields.database||user;
 if(database!==runtime)return {ok:false,message:`The developer login connects to its own database, ${runtime}`};
 return {ok:true,user,database};
}

function errorPacket(message:string) {
 const fields=Buffer.concat([Buffer.from('SFATAL\0VFATAL\0C28000\0M'),Buffer.from(message),Buffer.from('\0\0')]);
 const header=Buffer.alloc(5);header.write('E',0);header.writeInt32BE(fields.length+4,1);
 return Buffer.concat([header,fields]);
}

export function databaseProxy(options:{port:number;host:string;target:()=>{host:string;port:number}|undefined;
 log?:(line:string)=>void}) {
 const sockets=new Set<Socket>();
 const server=createServer(client=>{
  sockets.add(client);client.once('close',()=>sockets.delete(client));
  client.on('error',()=>client.destroy());
  let buffer=Buffer.alloc(0);
  const timer=setTimeout(()=>client.destroy(),10_000);
  const refuse=(message:string)=>{clearTimeout(timer);options.log?.(`database refused: ${message}`);client.end(errorPacket(message));};
  const onData=(chunk:Buffer)=>{
   buffer=Buffer.concat([buffer,chunk]);
   while(buffer.length>=8){
    const length=buffer.readInt32BE(0),code=buffer.readInt32BE(4);
    if(length<8||length>MAX_STARTUP)return refuse('Invalid startup message');
    if(buffer.length<length)return;
    if(length===8&&(code===SSL_REQUEST||code===GSS_REQUEST)){client.write('N');buffer=buffer.subarray(8);continue;}
    const message=buffer.subarray(0,length);buffer=buffer.subarray(length);
    if(code===CANCEL_REQUEST)return forward(message,undefined);
    if(code!==PROTOCOL_3)return refuse('Unsupported protocol version');
    const decision=decide(startupFields(message.subarray(8)));
    if(!decision.ok)return refuse(decision.message);
    return forward(message,decision.user);
   }
  };
  const forward=(first:Buffer,user:string|undefined)=>{
   clearTimeout(timer);client.off('data',onData);client.pause();
   const target=options.target();
   if(!target)return refuse('The database is not running');
   const upstream=connect(target.port,target.host,()=>{
    upstream.write(first);
    if(buffer.length)upstream.write(buffer);
    client.pipe(upstream);upstream.pipe(client);client.resume();
    if(user)options.log?.(`database connection for ${user}`);
   });
   const close=()=>{client.destroy();upstream.destroy();};
   upstream.once('error',()=>{if(!client.destroyed)client.end(errorPacket('The database is not reachable'));upstream.destroy();});
   client.once('close',close);upstream.once('close',()=>client.destroy());
  };
  client.on('data',onData);
 });
 server.maxConnections=64;
 return new Promise<{port:number;stop:()=>void}>((resolve,reject)=>{
  server.once('error',reject);
  server.listen(options.port,options.host,()=>{
   server.off('error',reject);
   const address=server.address();
   resolve({port:typeof address==='object'&&address?address.port:options.port,stop(){for(const socket of sockets)socket.destroy();server.close();}});
  });
 });
}

/** The listener's port and address from SBARBASE_DATABASE_PORT (6543 by default, as Supabase's pooler)
 * and SBARBASE_DATABASE_BIND (127.0.0.1 by default). A port of `off` turns it off. */
export function databaseListen(env:Record<string,string|undefined>=process.env):{port:number;host:string}|null {
 const value=(env.SBARBASE_DATABASE_PORT??'').trim();
 if(value==='off')return null;
 const port=value===''?6543:Number(value);
 if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('SBARBASE_DATABASE_PORT must be a port from 1024 to 65535, or off');
 const host=(env.SBARBASE_DATABASE_BIND??'').trim()||'127.0.0.1';
 if(!/^(\d{1,3}(\.\d{1,3}){3}|::|::1|localhost)$/.test(host))throw new Error('SBARBASE_DATABASE_BIND must be an IP address');
 return {port,host};
}

import {afterAll,beforeAll,expect,test} from 'bun:test';
import {createServer,type Server,type Socket} from 'node:net';
import {WireSession} from '../src/scheduler/wire';

// A fake PostgreSQL server: it accepts without a password (AuthenticationOk), then answers each query with
// whatever the test scripted, written in many small chunks so messages straddle reads.
const message=(type:string,body:Buffer=Buffer.alloc(0))=>{const head=Buffer.alloc(5);head.write(type,0,'latin1');head.writeInt32BE(body.length+4,1);return Buffer.concat([head,body]);};
const int32=(value:number)=>{const out=Buffer.alloc(4);out.writeInt32BE(value);return out;};
const cstring=(text:string)=>Buffer.from(text+'\0');
const ready=()=>message('Z',Buffer.from('I'));
let server:Server,port=0,script:Buffer[]=[],received:string[]=[];
function trickle(socket:Socket,data:Buffer){for(let i=0;i<data.length;i+=7919)socket.write(data.subarray(i,i+7919));}
beforeAll(async()=>{
 server=createServer(socket=>{
  let startup=true,pending=Buffer.alloc(0);
  socket.on('data',(chunk:Buffer)=>{
   pending=Buffer.concat([pending,chunk]);
   if(startup){
    if(pending.length<4||pending.length<pending.readInt32BE(0))return;
    pending=pending.subarray(pending.readInt32BE(0));startup=false;
    trickle(socket,Buffer.concat([message('R',int32(0)),message('K',Buffer.concat([int32(4242),int32(7)])),ready()]));
   }
   while(pending.length>=5&&pending.length>=1+pending.readInt32BE(1)){
    const type=String.fromCharCode(pending[0]!),length=pending.readInt32BE(1);
    received.push(type+pending.subarray(5,1+length).toString('latin1').replace(/\0$/,''));
    pending=pending.subarray(1+length);
    if(type==='Q')trickle(socket,script.shift()!);
   }
  });
 });
 await new Promise<void>(done=>server.listen(0,'127.0.0.1',done));
 port=(server.address() as {port:number}).port;
});
afterAll(()=>server.close());
const open=()=>WireSession.open({host:'127.0.0.1',port,user:'u',database:'d',password:'p',application:'t'});

test('the backend pid comes from BackendKeyData, and the last tag and the rows of the last result are kept', async()=>{
 const session=await open();
 expect(session.pid).toBe(4242);
 const row=message('D',Buffer.concat([Buffer.from([0,1]),int32(1),Buffer.from('x')]));
 script.push(Buffer.concat([message('T',Buffer.from([0,0])),row,row,message('C',cstring('SELECT 2')),message('C',cstring('INSERT 0 1')),
  message('T',Buffer.from([0,0])),row,message('C',cstring('SELECT 1')),ready()]));
 expect(await session.query('x')).toEqual({ok:true,tag:'SELECT 1',rows:1,tuples:true});
 session.close();
});

test('rows far larger than the cap are dropped as they stream, not held', async()=>{
 const session=await open();
 const big=message('D',Buffer.concat([Buffer.from([0,1]),int32(3*1024*1024),Buffer.alloc(3*1024*1024,120)]));
 script.push(Buffer.concat([message('T',Buffer.from([0,0])),big,big,big,message('N',Buffer.alloc(2*1024*1024,65)),message('C',cstring('SELECT 3')),ready()]));
 expect(await session.query('x')).toEqual({ok:true,tag:'SELECT 3',rows:3,tuples:true});
 script.push(Buffer.concat([message('C',cstring('VACUUM')),ready()]));
 expect(await session.query('vacuum')).toEqual({ok:true,tag:'VACUUM',rows:0,tuples:false});
 session.close();
});

test('the first error is the outcome as libpq writes it, and COPY is refused in both directions as pg_cron refuses it', async()=>{
 const session=await open();
 const error=(text:string,detail?:string)=>message('E',Buffer.concat([cstring('SERROR'),cstring('M'+text),...(detail?[cstring('D'+detail)]:[]),Buffer.from([0])]));
 script.push(Buffer.concat([error('division by zero','why'),error('second'),ready()]));
 expect(await session.query('x')).toEqual({ok:false,message:'ERROR:  division by zero\nDETAIL:  why\n'});
 received=[];
 script.push(Buffer.concat([message('G',Buffer.from([0,0,0])),error('COPY from stdin failed'),ready()]));
 expect(await session.query('copy t from stdin')).toEqual({ok:false,message:'COPY not supported'});
 for(let i=0;i<50&&!received.some(item=>item.startsWith('fCOPY not supported'));i++)await Bun.sleep(10);
 expect(received.some(item=>item.startsWith('fCOPY not supported'))).toBe(true);
 script.push(Buffer.concat([message('H',Buffer.from([0,0,0])),message('d',Buffer.alloc(5*1024*1024,1)),message('c'),message('C',cstring('COPY 9')),ready()]));
 expect(await session.query('copy t to stdout')).toEqual({ok:false,message:'COPY not supported'});
 expect(session.ended).toBe(true);
 session.close();
});

test('a connection that closes mid-query ends the query with an error instead of hanging', async()=>{
 const session=await open();
 script.push(message('T',Buffer.from([0,0])));
 const outcome=session.query('x');
 setTimeout(()=>session.close(),100);
 expect(await outcome).toMatchObject({ok:false});
});

test('a stream of notices or COPY data never keeps the event loop from running timers', async()=>{
 const session=await open();
 const notices=Buffer.concat(Array.from({length:200000},()=>message('N',Buffer.concat([cstring('SNOTICE'),cstring('Mx'),Buffer.from([0])]))));
 script.push(Buffer.concat([notices,message('C',cstring('DO')),ready()]));
 let fired=0;const timer=setInterval(()=>fired++,2);
 const outcome=await session.query('do');
 clearInterval(timer);
 expect(outcome).toEqual({ok:true,tag:'DO',rows:0,tuples:false});
 expect(fired).toBeGreaterThan(0);
 session.close();
});

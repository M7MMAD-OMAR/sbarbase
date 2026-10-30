import {expect,test} from 'bun:test';

async function load() {
 (globalThis as any).location={origin:'https://console.test'};
 return await import('../ui/api');
}
async function withFetch<T>(answers:(Response|Error)[],run:(calls:{url:string;authorization:string}[])=>Promise<T>):Promise<T> {
 const calls:{url:string;authorization:string}[]=[];const real=globalThis.fetch;
 globalThis.fetch=(async(url:any,init:any)=>{calls.push({url:String(url),authorization:init.headers.authorization});
  const answer=answers[calls.length-1]??new Response('{}');if(answer instanceof Error)throw answer;return answer;}) as any;
 try{return await run(calls);}finally{globalThis.fetch=real;}
}
const refused=(status:number,message?:unknown)=>new Response(JSON.stringify(message===undefined?{}:{message}),{status,headers:{'content-type':'application/json'}});

test('a refusal keeps the console sentence and carries the server message and status for screens that use it',async()=>{
 const {api,ApiError}=await load();
 for(const [status,sentence] of [[400,'Check the entered values and try again.'],[422,'Check the entered values and try again.'],[403,'You no longer have permission for this action.'],
  [429,'Too many requests. Wait a moment and try again.'],[503,'The server is temporarily unavailable. Try again.'],[404,'The request failed. Refresh and try again.']] as const){
  const error=await withFetch([refused(status,'Check the files: an index.ts')],()=>api('t')('/x').then(()=>null,reason=>reason));
  expect(error).toBeInstanceOf(ApiError);
  expect(error.message).toBe(sentence);
  expect(error.status).toBe(status);
  expect(error.serverMessage).toBe('Check the files: an index.ts');
 }
});

test('a server message that is not short plain text is dropped, and a missing one is undefined',async()=>{
 const {api}=await load();
 for(const message of ['x'.repeat(301),{nested:true},42,undefined]){
  const error=await withFetch([refused(400,message)],()=>api('t')('/x').then(()=>null,reason=>reason));
  expect(error.serverMessage).toBeUndefined();
  expect(error.message).toBe('Check the entered values and try again.');
 }
 const html=await withFetch([new Response('<html>',{status:502})],()=>api('t')('/x').then(()=>null,reason=>reason));
 expect(html.serverMessage).toBeUndefined();
});

test('a conflict keeps its known sentence and still carries the raw message',async()=>{
 const {api}=await load();
 const known=await withFetch([refused(409,'Name already used')],()=>api('t')('/x').then(()=>null,reason=>reason));
 expect(known.message).toBe('That name is already used here. Choose another name.');
 expect(known.serverMessage).toBe('Name already used');
 const unknown=await withFetch([refused(409,'The new version did not start')],()=>api('t')('/x').then(()=>null,reason=>reason));
 expect(unknown.message).toBe('This environment is not provisioned yet.');
 expect(unknown.serverMessage).toBe('The new version did not start');
});

test('a download hands back the bytes and the file name, and a refusal carries the message',async()=>{
 const {download}=await load();
 const bytes=new Uint8Array([80,75,3,4,0,255]);
 const answer=new Response(bytes,{status:200,headers:{'content-type':'application/zip','content-disposition':'attachment; filename="hello.zip"'}});
 const file=await withFetch([answer],async calls=>{const value=await download('t')('/environments/e/functions/hello/download');expect(calls[0]!.url).toBe('/management/v1/environments/e/functions/hello/download');return value;});
 expect(file.filename).toBe('hello.zip');
 expect(new Uint8Array(await file.blob.arrayBuffer())).toEqual(bytes);
 const odd=await withFetch([new Response('x',{status:200,headers:{'content-disposition':'attachment; filename="../../etc/passwd"'}})],()=>download('t')('/x'));
 expect(odd.filename).toBe('download');
 const error=await withFetch([refused(404,'Function not found')],()=>download('t')('/x').then(()=>null,reason=>reason));
 expect(error.status).toBe(404);expect(error.serverMessage).toBe('Function not found');
});

test('a download refreshes an expired token once, like every other call',async()=>{
 const {download,auth}=await load();
 (auth.auth as any).refreshSession=async()=>({data:{session:{access_token:'fresh'}},error:null});
 const file=await withFetch([new Response('',{status:401}),new Response('zip',{status:200,headers:{'content-disposition':'attachment; filename="a.zip"'}})],async calls=>{
  const value=await download('stale')('/x');expect(calls.map(call=>call.authorization)).toEqual(['Bearer stale','Bearer fresh']);return value;});
 expect(file.filename).toBe('a.zip');
});

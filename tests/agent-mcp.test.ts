import {test,expect} from 'bun:test';
import {mcpEndpoint} from '../src/control/mcp';
import {serveLocal} from '../src/http/local-server';
import {READ_PERMISSIONS} from '../src/control/agent-permissions';
import type {AccessToken} from '../src/control/access-tokens';

const bearer='sbp_'+'a'.repeat(40);
const token:AccessToken={id:'11111111-1111-4111-8111-111111111111',name:'reader',actor:'owner',scope:{kind:'all'},permissions:[...READ_PERMISSIONS],read_only:true,created_at:0,expires_at:Date.now()+60_000,last_used_at:null,revoked_at:null};
async function setup(){
 const calls:{path:string;authorization:string|null;body:unknown}[]=[];
 const api=await serveLocal(async request=>{
  const path=new URL(request.url).pathname;
  calls.push({path,authorization:request.headers.get('authorization'),body:request.method==='POST'?await request.json():null});
  if(path==='/v1/agent/operations')return Response.json({permissions:token.permissions,operations:[{name:'auth.settings.read',permission:'auth.read'}]});
  if(path==='/v1/agent/execute')return Response.json({message:'This token requires auth.write'},{status:403});
  return Response.json([]);
 });
 let live=true;
 const endpoint=mcpEndpoint({loopback:()=>`http://127.0.0.1:${api.port}`,resolve:value=>live&&value===bearer?token:null,projectUrl:async()=>{throw new Error('Forbidden');}});
 const rpc=async(method:string,params:unknown={},version='2025-06-18')=>{
  const response=await endpoint(new Request('http://local/mcp',{method:'POST',headers:{authorization:'Bearer '+bearer,'content-type':'application/json',accept:'application/json, text/event-stream','mcp-protocol-version':version,...(version==='2026-07-28'?{'mcp-method':method}:{})},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params:{...(params as object),...(version==='2026-07-28'?{_meta:{'io.modelcontextprotocol/protocolVersion':version,'io.modelcontextprotocol/clientInfo':{name:'test',version:'1'},'io.modelcontextprotocol/clientCapabilities':{}}}:{})}})}));
  const text=await response.text(),data=text.split('\n').find(line=>line.startsWith('data: '));
  return {status:response.status,body:JSON.parse(data?data.slice(6):text)};
 };
 return {rpc,calls,endpoint,revoke(){live=false;},close(){api.stop(true);}};
}
test('MCP adds administrative tools while retaining official read-only tools in both protocol eras',async()=>{
 const s=await setup();try{
  for(const version of ['2025-06-18','2026-07-28']){
   const listed=await s.rpc('tools/list',{},version);
   expect({status:listed.status,body:listed.body}).toMatchObject({status:200});
   const names=listed.body.result.tools.map((tool:any)=>tool.name);
   expect(names).toContain('list_projects');expect(names).toContain('get_sbarbase_operations');expect(names).toContain('execute_sbarbase_operation');
   expect(names).not.toContain('apply_migration');
  }
 }finally{s.close();}
});
test('administrative MCP calls the permission-checked API with the same bearer and surfaces refusal',async()=>{
 const s=await setup();try{
  const discovered=await s.rpc('tools/call',{name:'get_sbarbase_operations',arguments:{}});
  expect(discovered.body.result.isError).not.toBe(true);
  expect(discovered.body.result.content[0].text).toContain('auth.settings.read');
  expect(s.calls[0]).toEqual({path:'/v1/agent/operations',authorization:'Bearer '+bearer,body:null});
  const input={operation:'auth.settings.update',project_ref:'a'.repeat(20),input:{redirect_urls:['https://app.example/callback']}};
  const denied=await s.rpc('tools/call',{name:'execute_sbarbase_operation',arguments:input});
  expect(denied.body.result.isError).toBe(true);expect(s.calls[1]!.body).toEqual(input);
  expect(denied.body.result.content[0].text).toContain('auth.write');
  expect(JSON.stringify(denied.body)).not.toContain(bearer);
 }finally{s.close();}
});
test('malformed custom arguments and revoked tokens never dispatch administrative calls',async()=>{
 const s=await setup();try{
  for(const args of [{},{operation:'x',url:'http://attacker/'},{operation:5}]){
   const response=await s.rpc('tools/call',{name:'execute_sbarbase_operation',arguments:args});
   expect(response.body.result.isError).toBe(true);
  }
  expect(s.calls).toHaveLength(0);
  s.revoke();expect((await s.rpc('tools/call',{name:'get_sbarbase_operations',arguments:{}})).status).toBe(401);
  expect(s.calls).toHaveLength(0);
 }finally{s.close();}
});


test('MCP reauthenticates after streaming body arrival before any local tool runs',async()=>{
 const s=await setup();try{
  let body!:ReadableStreamDefaultController<Uint8Array>;
  const stream=new ReadableStream<Uint8Array>({start(controller){body=controller;}});
  const pending=s.endpoint(new Request('http://local/mcp',{method:'POST',headers:{authorization:'Bearer '+bearer,'content-type':'application/json',accept:'application/json, text/event-stream','mcp-protocol-version':'2025-06-18'},body:stream}));
  s.revoke();
  body.enqueue(new TextEncoder().encode(JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'get_project_url',arguments:{project_id:'a'.repeat(20)}}})));body.close();
  const response=await pending;
  expect(response.status).toBe(401);expect(s.calls).toHaveLength(0);
 }finally{s.close();}
});

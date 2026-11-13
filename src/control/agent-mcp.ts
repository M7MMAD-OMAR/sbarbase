import {Server,createMcpHandler} from '@modelcontextprotocol/server';

export type AgentMcpCaller={bearer:string;loopback:string;request:Request};
const tools=[{
 name:'get_sbarbase_operations',
 description:'Discover Sbarbase administrative operations allowed by this token. Read this before changing settings. The server enforces both capabilities and current organization membership.',
 inputSchema:{type:'object',properties:{},additionalProperties:false},
 annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:false},
},{
 name:'execute_sbarbase_operation',
 description:'Run a discovered Sbarbase administrative operation. Writes default to preview; set dry_run:false to apply. Preview checks permission and target admission only, not full input validation or simulation. Use auth.settings.read then auth.settings.update for Site URL and redirect URLs; preserve current settings and base_revision. Project/environment deletion and token minting are unavailable.',
 inputSchema:{type:'object',properties:{operation:{type:'string',minLength:1,maxLength:100},project_ref:{type:'string'},organization_id:{type:'string'},input:{type:'object'},dry_run:{type:'boolean',default:true}},required:['operation'],additionalProperties:false},
 annotations:{readOnlyHint:false,destructiveHint:true,idempotentHint:false,openWorldHint:false},
}];
export const isAgentMcpTool=(name:unknown)=>tools.some(tool=>tool.name===name);

/** Keep protocol encoding and validation in the SDK. The supplemental server delegates
 * the original tool list to the official server and routes only these two tools itself. */
export function agentMcpHandler(official:ReturnType<typeof createMcpHandler>){
 return createMcpHandler(context=>{
  const caller=context.authInfo?.extra?.agentCaller as AgentMcpCaller|undefined;
  if(!caller)throw new Error('Unauthenticated agent request');
  const server=new Server({name:'sbarbase-administration',version:'1.0.0'},{capabilities:{tools:{}}});
  server.setRequestHandler('tools/list',async()=>{
   const response=await official.fetch(caller.request.clone(),{authInfo:context.authInfo});
   const text=await response.text();
   const data=text.split('\n').find(line=>line.startsWith('data: '));
   const message=JSON.parse(data?data.slice(6):text);
   if(!response.ok||!message.result?.tools)throw new Error('The official tool list is unavailable');
   return {...message.result,tools:[...message.result.tools,...tools]};
  });
  server.setRequestHandler('tools/call',async request=>{
   const name=request.params.name,args=request.params.arguments??{};
   const invalid=(message:string)=>({content:[{type:'text' as const,text:message}],isError:true});
   if(!isAgentMcpTool(name))return invalid('Unknown administrative tool');
   if(!args||typeof args!=='object'||Array.isArray(args))return invalid('Send an arguments object');
   const fields=name==='get_sbarbase_operations'?[]:['operation','project_ref','organization_id','input','dry_run'];
   if(Object.keys(args).some(key=>!fields.includes(key)))return invalid('Unknown tool argument');
   if(name==='execute_sbarbase_operation'&&(typeof args.operation!=='string'||!args.operation||args.operation.length>100))return invalid('Give a valid operation name from get_sbarbase_operations');
   const read=name==='get_sbarbase_operations';
   try{
    const response=await fetch(new URL(read?'/v1/agent/operations':'/v1/agent/execute',caller.loopback),{
     method:read?'GET':'POST',headers:{authorization:`Bearer ${caller.bearer}`,...(read?{}:{'content-type':'application/json'})},
     ...(read?{}:{body:JSON.stringify(args)}),signal:AbortSignal.any([caller.request.signal,AbortSignal.timeout(30_000)]),redirect:'error',
    });
    const value=await response.json();
    return {content:[{type:'text' as const,text:JSON.stringify(value)}],...(!response.ok?{isError:true}:{})};
   }catch{return invalid('The administrative API is unavailable; read current state before retrying a write');}
  });
  return server;
 },{legacy:'stateless'});
}

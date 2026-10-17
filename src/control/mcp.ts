import {createSupabaseMcpServer} from '@supabase/mcp-server-supabase';
import {createSupabaseApiPlatform} from '@supabase/mcp-server-supabase/platform/api';
import {createMcpHandler} from '@modelcontextprotocol/server';
import {reply} from './auth';
import type {AccessToken} from './access-tokens';
import {bounded} from './supabase-api';

/** The MCP endpoint, `POST /mcp` (docs/guides/agents.md): Supabase's own MCP server, from the
 * official `@supabase/mcp-server-supabase` package, run here so an agent needs only this address
 * and an access token. Its tools are the ones Supabase's hosted MCP has (projects, SQL,
 * migrations, Edge Functions, keys, logs, advisors), and each one calls this server's
 * Supabase-compatible API (src/control/supabase-api.ts) over loopback with the caller's own
 * token, so every check there applies. Two things are set here: a project's URL is its address on
 * this server (the package would name a supabase.co one), and a read-only token gets the
 * read-only tool set.
 *
 * Both MCP protocol generations are answered, the 2026 stateless one and the 2025 one with its
 * initialize handshake, which most clients still speak. Nothing is kept between requests: each
 * request gets a server built for its own token. */

export const MCP_FEATURES=['account','database','debugging','development','functions'];

export type McpOptions={
 /** This server's own loopback address, where the Supabase-compatible API answers. */
 loopback:()=>string|undefined;
 /** The token in the request, checked; null when it is not a live access token. */
 resolve:(token:string)=>AccessToken|null;
 /** The rate limit: a token's own budget, or the shared one for requests without a valid token. */
 admit?:(token:AccessToken|null)=>boolean;
 /** A project's API address on this server, by ref. */
 projectUrl:(token:AccessToken,ref:string)=>Promise<string>;
};

type Caller={bearer:string;token:AccessToken;loopback:string};

export function mcpEndpoint(options:McpOptions) {
 const handler=createMcpHandler(context=>{
  const caller=context.authInfo?.extra?.caller as Caller|undefined;
  if(!caller)throw new Error('Unauthenticated MCP request');
  const platform=createSupabaseApiPlatform({accessToken:caller.bearer,apiUrl:caller.loopback});
  platform.development={...platform.development!,getProjectUrl:ref=>options.projectUrl(caller.token,ref)};
  // Two tools this server cannot answer: free ClickHouse log queries (get_logs, which it can, is
  // listed instead) and the health advisors on Supabase's v2 API.
  platform.debugging={...platform.debugging!,queryLogs:undefined,getHealthAdvisors:undefined};
  return createSupabaseMcpServer({platform,readOnly:caller.token.read_only,features:MCP_FEATURES});
 },{legacy:'stateless'});
 return async(request:Request):Promise<Response>=>{
  const bearer=/^Bearer (\S+)$/i.exec(request.headers.get('authorization')??'')?.[1]??'';
  const token=options.resolve(bearer);
  if(options.admit&&!options.admit(token))return reply(429,{message:'Too many requests'});
  if(!token)return Response.json({message:'Unauthorized: send a Sbarbase access token (sbp_...) as Authorization: Bearer'},
   {status:401,headers:{'cache-control':'no-store','www-authenticate':'Bearer'}});
  const loopback=options.loopback();
  if(!loopback)return reply(503,{message:'The MCP endpoint is starting; try again shortly'});
  // An MCP message is small; tool arguments carry SQL or function files, up to Edge Functions'
  // limit. The body is read within that limit before the handler sees it.
  let body:Buffer;
  try{body=await bounded(request,10*1024*1024,'Request too large');}
  catch(error){const status=(error as {status?:number}).status??400;return reply(status,{message:error instanceof Error?error.message:'Request body unavailable'});}
  const copy=new Request(request.url,{method:request.method,headers:request.headers,signal:request.signal,
   ...(request.method==='GET'||request.method==='HEAD'?{}:{body:new Uint8Array(body)})});
  return handler.fetch(copy,{authInfo:{token:bearer,clientId:token.id,scopes:token.read_only?['read']:['read','write'],
   expiresAt:Math.floor(token.expires_at/1000),extra:{caller:{bearer,token,loopback} satisfies Caller}}});
 };
}

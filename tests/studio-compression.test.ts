import {test,expect,afterEach} from 'bun:test';
import {gzipSync} from 'node:zlib';
import {studioProxy,studioHost,signStudio,useStudioHosts,studioCookie} from '../src/control/studio';
import {studioHosts} from '../src/control/studio-host';

/** Bun's fetch decodes a compressed body but keeps its content-encoding header. A proxy that lets fetch decode
 * therefore hands the next hop plain bytes labelled gzip, and the browser (or the TLS proxy) fails with a
 * decompression error. Studio compresses its pages, so this broke every page behind a public address. */
const key=Buffer.alloc(32,9);
const A='e_'+'c'.repeat(24);
const servers:{stop:(force?:boolean)=>void}[]=[];
afterEach(()=>{useStudioHosts(studioHosts(undefined));for(const server of servers.splice(0))server.stop(true);});

test('a gzip page from Studio reaches the next hop intact',async()=>{
 const page='<html>'+'studio '.repeat(500)+'</html>';
 const studio=Bun.serve({port:0,fetch:()=>new Response(gzipSync(page),{headers:{'content-encoding':'gzip','content-type':'text/html'}})});servers.push(studio);
 const proxy=studioProxy({key:()=>key,epoch:()=>0,allowed:()=>true,upstream:runtime=>runtime===A?`http://127.0.0.1:${studio.port}`:undefined});
 useStudioHosts(studioHosts('{id}.studio.localhost'));
 const cookie=`${studioCookie()}=${signStudio(key,{runtime:A,actor:'actor',expires:Date.now()+60_000,kind:'session',epoch:0})}`;
 const hop=Bun.serve({port:0,fetch:()=>proxy(new Request(`http://${studioHost(A)}:8787/`,{headers:{cookie,'accept-encoding':'gzip'}}))});servers.push(hop);
 // A normal client decodes whatever the proxy sends; a body and header that disagree make this throw.
 const response=await fetch(`http://127.0.0.1:${hop.port}/`);
 expect(response.status).toBe(200);
 expect(await response.text()).toBe(page);
});

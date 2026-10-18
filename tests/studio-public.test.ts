import {test,expect,afterEach} from 'bun:test';
import {studioProxy,studioHost,studioRuntime,signStudio,useStudioHosts,studioCookie} from '../src/control/studio';
import {studioHosts} from '../src/control/studio-host';

const key=Buffer.alloc(32,7);
const A='e_'+'a'.repeat(24),B='e_'+'b'.repeat(24);
afterEach(()=>useStudioHosts(studioHosts(undefined)));

test('the Studio address pattern: the tunnel default, a public pattern, and refused patterns',()=>{
 const local=studioHosts(undefined);
 expect(local.public).toBe(false);expect(local.host(A)).toBe('a'.repeat(24)+'.studio.localhost');
 const pub=studioHosts('studio-{id}.example.com');
 expect(pub.public).toBe(true);
 expect(pub.host(A)).toBe('studio-'+'a'.repeat(24)+'.example.com');
 expect(pub.runtime('studio-'+'a'.repeat(24)+'.example.com')).toBe(A);
 expect(pub.runtime('STUDIO-'+'A'.repeat(24)+'.EXAMPLE.COM')).toBe(A);
 for(const name of ['studio-xyz.example.com','x.studio-'+'a'.repeat(24)+'.example.com','studio-'+'a'.repeat(24)+'.example.com.evil.net','example.com'])
  expect(pub.runtime(name)).toBeNull();
 for(const bad of ['studio.example.com','{id}{id}.example.com','{id}','studio_{id}.example.com','studio-{id}.exa mple.com','https://studio-{id}.example.com'])
  expect(()=>studioHosts(bad)).toThrow();
});

function proxyFor(seen:Request[]) {
 return studioProxy({key:()=>key,epoch:()=>0,allowed:()=>true,upstream:runtime=>runtime===A?'http://studio-a.internal:3000':undefined,
  transport:(async(input:URL|RequestInfo,init?:RequestInit)=>{seen.push(new Request(input,init));return new Response('studio page');}) as typeof fetch});
}

test('on a public address the cookie is __Host-, Secure and host-only, and a ticket works once',async()=>{
 useStudioHosts(studioHosts('studio-{id}.example.com'));
 expect(studioHost(A)).toBe('studio-'+'a'.repeat(24)+'.example.com');
 expect(studioRuntime(studioHost(A))).toBe(A);
 const seen:Request[]=[];const proxy=proxyFor(seen);
 const ticket=signStudio(key,{runtime:A,actor:'alice',epoch:0,expires:Date.now()+60_000,kind:'ticket'});
 const url=`http://${studioHost(A)}/__sbarbase/enter?ticket=${encodeURIComponent(ticket)}`;
 const enter=await proxy(new Request(url));
 expect(enter.status).toBe(302);
 const cookie=enter.headers.get('set-cookie')!;
 expect(cookie.startsWith('__Host-sbarbase_studio=')).toBe(true);
 expect(cookie).toContain('Secure');expect(cookie).toContain('Path=/');expect(cookie).not.toContain('Domain');
 expect(studioCookie()).toBe('__Host-sbarbase_studio');
 // The same link again, as if copied from history or a chat, opens nothing.
 expect((await proxy(new Request(url))).status).toBe(401);
 const session=cookie.split(';')[0]!;
 expect((await proxy(new Request(`http://${studioHost(A)}/project/default`,{headers:{cookie:session}}))).status).toBe(200);
 // The tunnel name is not accepted once a public pattern is set.
 expect(studioRuntime('a'.repeat(24)+'.studio.localhost')).toBeNull();
});

test('a change must come from the Studio page itself: a sibling site on the same domain is refused',async()=>{
 useStudioHosts(studioHosts('studio-{id}.example.com'));
 const seen:Request[]=[];const proxy=proxyFor(seen);
 const ticket=signStudio(key,{runtime:A,actor:'alice',epoch:0,expires:Date.now()+60_000,kind:'ticket'});
 const session=(await proxy(new Request(`http://${studioHost(A)}/__sbarbase/enter?ticket=${encodeURIComponent(ticket)}`))).headers.get('set-cookie')!.split(';')[0]!;
 const post=(headers:Record<string,string>)=>proxy(new Request(`http://${studioHost(A)}/api/platform/pg-meta/default/query`,{method:'POST',headers:{cookie:session,'content-type':'application/json',...headers},body:'{"query":"drop table x"}'}));
 for(const headers of <Record<string,string>[]>[{origin:'https://blog.example.com'},{origin:'https://'+studioHost(B)},{origin:'http://'+studioHost(A)},{},{'sec-fetch-site':'same-site'}]){
  expect((await post(headers)).status).toBe(403);
 }
 expect(seen).toHaveLength(0);
 expect((await post({origin:'https://'+studioHost(A)})).status).toBe(200);
 expect((await post({'sec-fetch-site':'same-origin'})).status).toBe(200);
 expect(seen).toHaveLength(2);
 // Reading needs no Origin, as a page load sends none.
 expect((await proxy(new Request(`http://${studioHost(A)}/project/default`,{headers:{cookie:session}}))).status).toBe(200);
});

test('through the tunnel the cookie keeps its name and same-origin writes still work',async()=>{
 const seen:Request[]=[];const proxy=proxyFor(seen);
 const ticket=signStudio(key,{runtime:A,actor:'alice',epoch:0,expires:Date.now()+60_000,kind:'ticket'});
 const enter=await proxy(new Request(`http://${studioHost(A)}:8790/__sbarbase/enter?ticket=${encodeURIComponent(ticket)}`));
 const cookie=enter.headers.get('set-cookie')!;
 expect(cookie.startsWith('sbarbase_studio=')).toBe(true);expect(cookie).not.toContain('Secure');
 const session=cookie.split(';')[0]!;
 const ok=await proxy(new Request(`http://${studioHost(A)}:8790/api/x`,{method:'POST',headers:{cookie:session,origin:`http://${studioHost(A)}:8790`},body:'{}'}));
 expect(ok.status).toBe(200);
 const refused=await proxy(new Request(`http://${studioHost(A)}:8790/api/x`,{method:'POST',headers:{cookie:session,origin:'http://localhost:8790'},body:'{}'}));
 expect(refused.status).toBe(403);
});

test('a ticket cannot be replayed by spelling its signature another way',async()=>{
 useStudioHosts(studioHosts('studio-{id}.example.com'));
 const seen:Request[]=[];const proxy=proxyFor(seen);
 const ticket=signStudio(key,{runtime:A,actor:'alice',epoch:0,expires:Date.now()+60_000,kind:'ticket'});
 const enter=(value:string)=>proxy(new Request(`http://${studioHost(A)}/__sbarbase/enter?ticket=${encodeURIComponent(value)}`));
 expect((await enter(ticket)).status).toBe(302);
 const last=ticket.slice(-1),other=last==='A'?'B':'A';
 for(const variant of [ticket+'=',ticket+'==',ticket+' ',ticket+'!',ticket.slice(0,-1)+other,ticket])
  expect((await enter(variant)).status).toBe(401);
 // A ticket without a nonce is never accepted.
 const body=Buffer.from(JSON.stringify({runtime:A,actor:'alice',epoch:0,expires:Date.now()+60_000,kind:'ticket'})).toString('base64url');
 const {createHmac}=require('node:crypto');
 expect((await enter(body+'.'+createHmac('sha256',key).update(body).digest('base64url'))).status).toBe(401);
});

test('requests another site starts: only a plain navigation, landing on the project home without its query',async()=>{
 useStudioHosts(studioHosts('studio-{id}.example.com'));
 const seen:Request[]=[];const proxy=proxyFor(seen);
 const ticket=signStudio(key,{runtime:A,actor:'alice',epoch:0,expires:Date.now()+60_000,kind:'ticket'});
 // The console on a sibling host opens Studio by navigation: allowed.
 const enter=await proxy(new Request(`http://${studioHost(A)}/__sbarbase/enter?ticket=${encodeURIComponent(ticket)}`,{headers:{'sec-fetch-site':'same-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'}}));
 expect(enter.status).toBe(302);
 const cookie=enter.headers.get('set-cookie')!.split(';')[0]!;
 const get=(path:string,headers:Record<string,string>)=>proxy(new Request(`http://${studioHost(A)}${path}`,{headers:{cookie,...headers}}));
 const image=await get('/api/platform/pg-meta/default/tables',{'sec-fetch-site':'same-site','sec-fetch-mode':'no-cors','sec-fetch-dest':'image'});
 expect(image.status).toBe(403);
 const link=await get('/project/default/sql/new?content=drop%20table%20x',{'sec-fetch-site':'cross-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'});
 expect(link.status).toBe(303);expect(link.headers.get('location')).toBe('/project/default');
 expect(seen).toHaveLength(0);
 expect((await get('/project/default',{'sec-fetch-site':'cross-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'})).status).toBe(200);
 expect((await get('/project/default/sql/new?content=x',{'sec-fetch-site':'same-origin','sec-fetch-mode':'navigate','sec-fetch-dest':'document'})).status).toBe(200);
});

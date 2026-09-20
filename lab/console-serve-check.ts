// Console static-serving check: prove the built page is served correctly and safely.
//
// Usage: bun lab/console-serve-check.ts
//
// Starts the same loopback adapter the console uses, on an ephemeral port, with
// the static layer in front of a marker fallback for everything else. No
// container, no secret, no fixed port. Evidence goes to
// docs/evidence/console-serve.json. Exit code is non-zero if any check fails.
import {createHash} from 'node:crypto';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {serveLocal} from '../src/http/local-server';
import {uiStatic} from './ui-static';

const BUILD_ROOT=resolve('.lab/ui');
const EVIDENCE=resolve('docs/evidence/console-serve.json');
const FALLTHROUGH='app-layer-fallthrough';

type Check={check:string;ok:boolean;detail:string};
const checks:Check[]=[];
function record(check:string,ok:boolean,detail=''){checks.push({check,ok,detail});console.log((ok?'ok:  ':'FAIL ')+check+(ok||!detail?'':'  '+detail));}

const handler=async(request:Request):Promise<Response>=>{
 const url=new URL(request.url);
 if(!['127.0.0.1','localhost'].includes(url.hostname))return new Response('Invalid host',{status:403});
 if(url.pathname==='/favicon.ico')return new Response(null,{status:204});
 return (await uiStatic(request))??new Response(FALLTHROUGH,{status:404,headers:{'content-type':'text/plain'}});
};

const server=await serveLocal(handler);
const base=`http://127.0.0.1:${server.port}`;

try {
 const index=await fetch(base+'/');
 const page=await index.text();
 record('built page is served as html',index.status===200&&(index.headers.get('content-type')??'').startsWith('text/html'),`status ${index.status} ${index.headers.get('content-type')}`);
 record('page is not cached by intermediaries',index.headers.get('cache-control')==='no-store',String(index.headers.get('cache-control')));
 const csp=index.headers.get('content-security-policy')??'';
 record('page carries a restrictive content security policy',csp.includes("default-src 'self'")&&csp.includes("frame-ancestors 'none'"),csp.slice(0,60));
 record('page forbids content sniffing',index.headers.get('x-content-type-options')==='nosniff');

 const references=[...page.matchAll(/(?:src|href)="([^"]+)"/g)].map(m=>m[1]).filter(v=>v&&!v.startsWith('http')&&!v.startsWith('data:'));
 record('page references at least one local asset',references.length>0,references.join(' '));
 for(const reference of references){
  const asset=await fetch(base+reference);
  const body=new Uint8Array(await asset.arrayBuffer());
  const onDisk=createHash('sha256').update(readFileSync(resolve(BUILD_ROOT,reference.replace(/^\//,'')))).digest('hex');
  const served=createHash('sha256').update(body).digest('hex');
  record('served asset matches the built file: '+reference,asset.status===200&&served===onDisk,`status ${asset.status} served ${served.slice(0,12)} disk ${onDisk.slice(0,12)}`);
  record('asset is announced immutable: '+reference,(asset.headers.get('cache-control')??'').includes('immutable'),String(asset.headers.get('cache-control')));
 }

 const head=await fetch(base+'/',{method:'HEAD'});
 record('HEAD returns headers without a body',head.status===200&&(await head.text()).length===0,`status ${head.status}`);

 const post=await fetch(base+'/',{method:'POST',body:'x'});
 record('a write to a static path is refused',post.status===405,`status ${post.status}`);

 const traversal=await fetch(base+'/../src/http/local-server.ts');
 const traversalBody=await traversal.text();
 record('path traversal serves no source file',traversal.status!==200&&!traversalBody.includes('serveLocal'),`status ${traversal.status}`);

 const unknown=await fetch(base+'/api/does-not-exist');
 record('non-static paths reach the application layer',unknown.status===404&&(await unknown.text())===FALLTHROUGH,`status ${unknown.status}`);

 const favicon=await fetch(base+'/favicon.ico');
 record('favicon is answered without a file',favicon.status===204,`status ${favicon.status}`);

 const spoofed=await fetch(base+'/',{headers:{host:'evil.example.com'}});
 record('a non-loopback host header is refused',spoofed.status===403,`status ${spoofed.status}`);
} finally {
 server.stop(true);
}

const passed=checks.length>0&&checks.every(item=>item.ok);
mkdirSync(resolve('docs/evidence'),{recursive:true});
writeFileSync(EVIDENCE,JSON.stringify({
 scope:'Console static-serving check: the built console page and its assets are served by the same loopback adapter the console uses, on an ephemeral port, with the security headers, method refusal, traversal refusal and loopback host check. Not the console API, not the runtime, not a browser render.',
 build_root:BUILD_ROOT,
 checks,count:checks.length,passed,
 run_at:new Date().toISOString(),
},null,1)+'\n');
console.log('evidence: '+EVIDENCE);
console.log('console serve check: '+(passed?'passed':'failed'));
process.exit(passed?0:1);
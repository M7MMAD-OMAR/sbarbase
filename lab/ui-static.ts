import {resolve} from 'node:path';
/** Only build artifacts are exposed; the workspace and private state are not. */
export async function uiStatic(request:Request,buildRoot=resolve('.lab/ui')):Promise<Response|undefined> {
 const path=new URL(request.url).pathname;
 const isIndex=path==='/',isAsset=/^\/assets\/[A-Za-z0-9_.-]+\.(js|css)$/.test(path);
 if(!isIndex&&!isAsset)return undefined;
 if(request.method!=='GET'&&request.method!=='HEAD')return new Response(null,{status:405});
 const file=Bun.file(resolve(buildRoot,isIndex?'index.html':path.slice(1)));
 if(!await file.exists())return new Response('Console build unavailable. Run bun run build:ui.',{status:503});
 const headers={
  'content-type':isIndex?'text/html; charset=utf-8':path.endsWith('.js')?'text/javascript; charset=utf-8':'text/css; charset=utf-8',
  'cache-control':isIndex?'no-store':'public, max-age=31536000, immutable',
  'x-content-type-options':'nosniff','referrer-policy':'no-referrer',
  'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
 };
 return new Response(request.method==='HEAD'?null:file,{headers});
}

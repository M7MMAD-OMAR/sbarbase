import {test,expect} from 'bun:test';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {uiStatic} from '../lab/ui-static';

test('static console exposes only index and generated asset names with a restrictive policy',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'sbarbase-ui-'));
 try {
  mkdirSync(join(dir,'assets'));writeFileSync(join(dir,'index.html'),'<html>console</html>');writeFileSync(join(dir,'assets/app-123.js'),'export const ready=true;');
  writeFileSync(join(dir,'assets/cactus-123.svg'),'<svg xmlns="http://www.w3.org/2000/svg"/>');
  const logo=await uiStatic(new Request('http://local/assets/cactus-123.svg'),dir);
  expect(logo?.status).toBe(200);expect(logo?.headers.get('content-type')).toBe('image/svg+xml');
  expect(logo?.headers.get('x-content-type-options')).toBe('nosniff');
  writeFileSync(join(dir,'assets/photo-123.webp'),new Uint8Array([82,73,70,70]));
  const photo=await uiStatic(new Request('http://local/assets/photo-123.webp'),dir);
  expect(photo?.status).toBe(200);expect(photo?.headers.get('content-type')).toBe('image/webp');
  const index=await uiStatic(new Request('http://local/'),dir);
  expect(index?.status).toBe(200);expect(index?.headers.get('cache-control')).toBe('no-store');
  expect(index?.headers.get('content-security-policy')).toContain("frame-ancestors 'none'");
  expect(index?.headers.get('content-security-policy')).toContain("script-src 'self'");
  const asset=await uiStatic(new Request('http://local/assets/app-123.js'),dir);
  expect(asset?.headers.get('content-type')).toContain('javascript');expect(await asset?.text()).toContain('ready=true');
  expect(await (await uiStatic(new Request('http://local/',{method:'HEAD'}),dir))?.text()).toBe('');
  expect((await uiStatic(new Request('http://local/',{method:'POST'}),dir))?.status).toBe(405);
  for(const path of ['/.secrets/upstream/runtime.json','/src/control/catalog.ts','/assets/nested/file.js','/assets/../index.html','/assets/%2e%2e%2fsecret.js'])
   expect(await uiStatic(new Request('http://local'+path),dir)).toBeUndefined();
 }finally{rmSync(dir,{recursive:true});}
});

import {test,expect} from 'bun:test';
import {readdirSync,readFileSync,existsSync} from 'node:fs';
import {docs,sources,diagrams,rewrite,DIRECTORIES,type Source} from '../src/docs';
import {render} from '../src/render';
import {links} from '../src/content';

// The docs as the site serves them. Built in memory, so this runs without a prior build.
const pages=docs();
const byUrl=new Map(pages.map(p=>[p.url,p]));
const home={'/':render('ar'),'/en/':render('en')};
const ids=(html:string)=>[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]!);
const LONG_DASH=/[\u2013\u2014]/;
const repoRoot=new URL('../../',import.meta.url);

test('every page of the bilingual set renders in both languages',()=>{
 const expected:string[]=['docs/README.md','docs/README.ar.md'];
 for(const dir of DIRECTORIES)for(const f of readdirSync(new URL(dir,repoRoot)))if(f.endsWith('.md'))expected.push(`${dir}/${f}`);
 expect(pages.map(p=>p.source.path).sort()).toEqual(expected.sort());
 for(const page of pages){
  expect(byUrl.has(page.other)).toBe(true);
  expect(page.title.length).toBeGreaterThan(0);
  expect(page.html).toContain(`<html lang="${page.source.lang}" dir="${page.source.lang==='ar'?'rtl':'ltr'}">`);
  expect(page.html).toContain(`href="${page.other}" lang="${page.source.lang==='ar'?'en':'ar'}"`);
 }
 expect(pages.filter(p=>p.source.lang==='ar')).toHaveLength(pages.length/2);
});

test('every internal link lands on a page and an id that exists',()=>{
 const broken:string[]=[];
 const diagramFiles=new Set(diagrams().map(d=>`/${d}`));
 for(const page of pages){
  for(const m of page.html.matchAll(/\b(href|src)="([^"]+)"/g)){
   const value=m[2]!.replaceAll('&amp;','&');
   if(/^https?:|^mailto:/.test(value))continue;
   const [path,fragment]=value.split('#') as [string,string|undefined];
   if(m[1]==='src'||/\.(svg|css|js|woff2|jpg|webp)$/.test(path)){
    if(!diagramFiles.has(path)&&!existsSync(new URL(`../public${path}`,import.meta.url))&&!['/style.css','/app.js'].includes(path))broken.push(`${page.url}: ${value}`);
    continue;
   }
   const target=path===''?page.html:byUrl.get(path)?.html??home[path as '/'|'/en/'];
   if(target===undefined){broken.push(`${page.url}: ${value}`);continue;}
   if(fragment&&!ids(target).includes(decodeURIComponent(fragment)))broken.push(`${page.url}: ${value}`);
  }
 }
 expect(broken).toEqual([]);
});

test('diagrams are drawn, not left as Mermaid source',()=>{
 // The site does not draw Mermaid; a diagram belongs in docs/diagrams/build.py, in both languages.
 for(const page of pages)expect(page.html).not.toContain('language-mermaid');
});

test('ids are unique on every page',()=>{
 for(const page of pages){const list=ids(page.html);expect(new Set(list).size).toBe(list.length);}
});

test('headings keep GitHub anchors',()=>{
 expect(ids(byUrl.get('/en/docs/guides/server-deployment/')!.html)).toContain('private-directory-permissions');
 expect(ids(byUrl.get('/docs/guides/studio/')!.html)).toContain('فتحه-من-أي-مكان');
});

test('links are rewritten to the site, the diagrams or GitHub',()=>{
 const set=new Map<string,Source>(sources().map(s=>[s.path,s]));
 const from='docs/guides/upgrades.md';
 expect(rewrite(from,'docker.md',set)).toBe('/en/docs/guides/docker/');
 expect(rewrite(from,'docker.ar.md#x',set)).toBe('/docs/guides/docker/#x');
 expect(rewrite(from,'../README.md',set)).toBe('/en/docs/');
 expect(rewrite(from,'../diagrams/hierarchy.svg',set)).toBe('/docs/diagrams/hierarchy.svg');
 expect(rewrite(from,'../engineering/HOST-PREFLIGHT.md',set)).toBe('https://github.com/M7MMAD-OMAR/sbarbase/blob/main/docs/engineering/HOST-PREFLIGHT.md');
 expect(rewrite(from,'../../src/gateway/handler.ts#L12',set)).toBe('https://github.com/M7MMAD-OMAR/sbarbase/blob/main/src/gateway/handler.ts#L12');
 expect(rewrite('docs/README.md','evidence/',set)).toBe('https://github.com/M7MMAD-OMAR/sbarbase/tree/main/docs/evidence/');
 expect(rewrite(from,'#the-start-guard',set)).toBe('#the-start-guard');
 expect(rewrite(from,'https://example.com/a.md',set)).toBe('https://example.com/a.md');
});

test('no long dashes, no external resources, and code reads left to right',()=>{
 for(const page of pages){
  expect(page.html).not.toMatch(LONG_DASH);
  expect(page.html).not.toMatch(/<(script|img|video|source)[^>]+src="(https?:)?\/\//);
  expect(page.html).not.toMatch(/<link rel="(stylesheet|preload|icon)"[^>]+href="(https?:)?\/\//);
  expect(page.html).not.toMatch(/<pre(?! dir="ltr")/);
  // the language switch line of the source becomes the page's own switch, not a duplicate
  expect(page.html).not.toMatch(/<p><a href="[^"]*">(English|العربية)<\/a><\/p>/);
 }
});

test('copy buttons copy their block verbatim, and the page carries their labels',()=>{
 const page=byUrl.get('/en/docs/guides/docker/')!.html;
 const blocks=[...page.matchAll(/<div class="code"><pre dir="ltr"><code[^>]*>([\s\S]*?)<\/code><\/pre><button type="button" class="copy" data-copy="([^"]*)"/g)];
 expect(blocks.length).toBeGreaterThan(0);
 for(const b of blocks)expect(b[2]!.replaceAll('&#10;','\n')).toBe(b[1]!);
 const data=JSON.parse(page.match(/<script id="page-data" type="application\/json">(.*?)<\/script>/s)![1]!);
 expect(data).toEqual({copy:'Copy',copied:'Copied'});
});

test('the home page buttons open the docs on this site',()=>{
 for(const lang of ['ar','en'] as const){
  const l=links(lang);
  for(const url of [l.docs,l.docker,l.quickstart,l.server]){
   expect(byUrl.has(url)).toBe(true);
   expect(home[lang==='ar'?'/':'/en/']).toContain(`href="${url}"`);
  }
 }
});

test('docs sources carry no long dashes',()=>{
 for(const file of ['../src/docs.ts','../src/layout.ts','../tests/docs.test.ts'])
  expect(readFileSync(new URL(file,import.meta.url),'utf8')).not.toMatch(LONG_DASH);
});

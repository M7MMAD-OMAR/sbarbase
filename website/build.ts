import {mkdir,cp,rm} from 'node:fs/promises';
import {render} from './src/render';
import {docs,diagrams,repoFile,sources} from './src/docs';
import {llms,llmsFull,changelogFeed} from './src/agents';
import {SITE} from './src/layout';
const out=(path:string)=>new URL(`./dist/${path}`,import.meta.url);
await rm(out(''),{recursive:true,force:true});
await mkdir(out('en'),{recursive:true});
await cp(new URL('./public',import.meta.url),out(''),{recursive:true});
await Bun.write(out('index.html'),render('ar'));
await Bun.write(out('en/index.html'),render('en'));
await Bun.write(out('style.css'),Bun.file(new URL('./src/style.css',import.meta.url)));
await Bun.write(out('app.js'),Bun.file(new URL('./src/app.js',import.meta.url)));
await Bun.write(out('404.html'),'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Page not found | Sbarbase</title><link rel="stylesheet" href="/style.css"><body><main class="section"><div class="wrap"><h1>Page not found.</h1><p class="lede"><a href="/">Return to Sbarbase</a></p></div></main></body></html>');
// The date of each source's last commit, for the sitemap and the pages' metadata. A
// shallow or missing checkout gives no date, and the pages then carry none.
const lastCommit=(...paths:string[])=>{
 const r=Bun.spawnSync(['git','log','-1','--format=%cs','--',...paths],{cwd:repoFile('').pathname});
 return r.exitCode===0&&r.stdout.toString().trim()||'';
};
const shallow=Bun.spawnSync(['git','rev-parse','--is-shallow-repository'],{cwd:repoFile('').pathname}).stdout.toString().trim()!=='false';
const modified:Record<string,string>=shallow?{}:Object.fromEntries(sources().map(s=>[s.path,lastCommit(s.path)]));
const homeModified=shallow?'':lastCommit('website/src','website/public/media');
// The docs, one folder per page, each also as Markdown, and the diagrams they show.
const pages=docs(modified);
for(const page of pages){
 await Bun.write(out(`${page.url.slice(1)}index.html`),page.html);
 await Bun.write(out(`${page.url.slice(1)}index.md`),page.markdown);
}
// For search tools and AI agents: llms.txt, the English docs in one file, and the changelog feeds.
await Bun.write(out('llms.txt'),llms(pages));
await Bun.write(out('llms-full.txt'),llmsFull(pages));
const released=modified['CHANGELOG.md']||new Date().toISOString().slice(0,10);
await Bun.write(out('docs/changelog/feed.xml'),changelogFeed('ar',released));
await Bun.write(out('en/docs/changelog/feed.xml'),changelogFeed('en',released));
for(const diagram of diagrams())await Bun.write(out(diagram),Bun.file(repoFile(diagram)));
// Every page, in both languages, with its alternate.
const entry=(path:string,alternates:[string,string],lastmod='')=>`<url><loc>${SITE}${path}</loc>${lastmod?`<lastmod>${lastmod}</lastmod>`:''}<xhtml:link rel="alternate" hreflang="ar" href="${SITE}${alternates[0]}"/><xhtml:link rel="alternate" hreflang="en" href="${SITE}${alternates[1]}"/></url>`;
const urls=[entry('/',['/','/en/'],homeModified),entry('/en/',['/','/en/'],homeModified),...pages.map(p=>entry(p.url,p.source.lang==='ar'?[p.url,p.other]:[p.other,p.url],modified[p.source.path]))];
await Bun.write(out('sitemap.xml'),`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">${urls.join('')}</urlset>\n`);
console.log(`Built Arabic and English pages and ${pages.length} docs pages.`);

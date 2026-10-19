import {mkdir,cp,rm} from 'node:fs/promises';
import {render} from './src/render';
import {docs,diagrams,repoFile} from './src/docs';
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
// The docs, one folder per page, and the diagrams they show.
const pages=docs();
for(const page of pages)await Bun.write(out(`${page.url.slice(1)}index.html`),page.html);
for(const diagram of diagrams())await Bun.write(out(diagram),Bun.file(repoFile(diagram)));
// Every page, in both languages, with its alternate.
const entry=(path:string,alternates:[string,string],lastmod='')=>`<url><loc>${SITE}${path}</loc>${lastmod?`<lastmod>${lastmod}</lastmod>`:''}<xhtml:link rel="alternate" hreflang="ar" href="${SITE}${alternates[0]}"/><xhtml:link rel="alternate" hreflang="en" href="${SITE}${alternates[1]}"/></url>`;
const urls=[entry('/',['/','/en/'],'2026-09-26'),entry('/en/',['/','/en/'],'2026-09-26'),...pages.map(p=>entry(p.url,p.source.lang==='ar'?[p.url,p.other]:[p.other,p.url]))];
await Bun.write(out('sitemap.xml'),`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">${urls.join('')}</urlset>\n`);
console.log(`Built Arabic and English pages and ${pages.length} docs pages.`);

import {test,expect} from 'bun:test';
import {readFileSync} from 'node:fs';
import {render} from '../src/render';
import {content} from '../src/content';

// The install commands, written out here on purpose rather than imported, so a
// change to the page has to be a deliberate change to this list too.
const INSTALL=[
 'sudo dnf install -y moby-engine git python3-cryptography && sudo systemctl enable --now docker',
 'sudo useradd -m sbarbase && sudo usermod -aG docker sbarbase && sudo install -d -o sbarbase -g sbarbase /opt/sbarbase && sudo -u sbarbase git clone https://github.com/M7MMAD-OMAR/sbarbase /opt/sbarbase',
 "sudo -u sbarbase -H bash -c 'curl -fsSL https://bun.sh/install | bash -s bun-v1.3.14'",
 'cd /opt/sbarbase && sudo -u sbarbase /usr/bin/python3 lab/operator_file.py /home/sbarbase/operator.json',
 'sudo deploy/server-acceptance.sh --rehearse --install-unit --first-project --service-user sbarbase --home /home/sbarbase --bun-dir /home/sbarbase/.bun/bin --bootstrap-file /home/sbarbase/operator.json',
];
const unescape=(s:string)=>s.replaceAll('&quot;','"').replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
const LONG_DASH=/[\u2013\u2014]/;

for(const lang of ['ar','en'] as const){
 const c=content[lang];
 const html=render(lang);

 test(`${lang}: ids are unique and every in-page link lands`,()=>{
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  expect(new Set(ids).size).toBe(ids.length);
  for(const link of html.matchAll(/href="#([^"]+)"/g))expect(ids).toContain(link[1]);
  for(const id of ['hierarchy','how','install','explainer'])expect(ids).toContain(id);
 });

 test(`${lang}: language, direction and the other language are declared`,()=>{
  expect(html).toContain(`<html lang="${lang}" dir="${lang==='ar'?'rtl':'ltr'}">`);
  expect(html).toContain(`href="${lang==='ar'?'/en/':'/'}" lang="${lang==='ar'?'en':'ar'}"`);
  expect(html).toContain(`<link rel="canonical" href="https://base.sbarah.com${lang==='ar'?'/':'/en/'}">`);
 });

 test(`${lang}: the walkthrough data agrees with its buttons`,()=>{
  const data=JSON.parse(html.match(/<script id="page-data" type="application\/json">(.*?)<\/script>/s)![1]!);
  expect(data.steps).toHaveLength(5);
  expect(data.steps).toEqual(c.steps);
  expect([...html.matchAll(/<li data-walk="\d+"><button type="button"/g)]).toHaveLength(5);
  for(const id of ['w1','w2','w3','walk-play','walk-seek','walk-replay'])expect(html).toContain(`id="${id}"`);
 });

 test(`${lang}: the install section holds exactly the five real commands`,()=>{
  const section=html.match(/<section class="section" id="install"[\s\S]*?<\/section>/)![0];
  const codes=[...section.matchAll(/<code>([\s\S]*?)<\/code>/g)].map(m=>unescape(m[1]!));
  expect(codes).toEqual(INSTALL);
  const copies=[...section.matchAll(/data-copy="([^"]*)"/g)].map(m=>unescape(m[1]!));
  expect(copies).toEqual(INSTALL);
  expect([...html.matchAll(/<code>/g)]).toHaveLength(5);
 });

 test(`${lang}: exactly one maturity badge`,()=>{
  expect([...html.matchAll(/class="badge"/g)]).toHaveLength(1);
  expect(html).toContain(lang==='ar'?'مفتوح المصدر · قيد التطوير':'Open source · in development');
  expect(html.split(lang==='ar'?'قيد التطوير':'in development')).toHaveLength(2);
 });

 test(`${lang}: no long dashes and no external resources`,()=>{
  expect(html).not.toMatch(LONG_DASH);
  expect(html).not.toMatch(/<script[^>]+src="(https?:)?\/\//);
  expect(html).not.toMatch(/<link rel="(stylesheet|preload|icon)"[^>]+href="(https?:)?\/\//);
  expect(html).not.toMatch(/<(video|source|img)[^>]+(src|poster)="(https?:)?\/\//);
  expect(html).toContain('<video id="explainer" src="/media/explainer.mp4" poster="/media/explainer-poster.jpg" muted playsinline controls preload="metadata"');
  // captions in both languages, the page's own language on by default, and every media file shipped
  expect(html).toContain(`<track kind="captions" src="/media/explainer.${lang}.vtt" srclang="${lang}" label="${lang==='ar'?'العربية':'English'}" default>`);
  for(const f of ['explainer.mp4','explainer-poster.jpg','explainer.ar.vtt','explainer.en.vtt'])expect(()=>readFileSync(new URL(`../public/media/${f}`,import.meta.url))).not.toThrow();
 });
}

test('the Arabic request figure is the English one mirrored',()=>{
 const x=(html:string)=>Number(html.match(/<circle class="packet" r="9" cx="([\d.]+)"/)![1]);
 expect(x(render('ar'))).toBe(900-x(render('en')));
});

test('the Arabic hierarchy figure is the English one mirrored',()=>{
 const rects=(html:string)=>[...html.match(/<svg class="figure hier-figure"[\s\S]*?<\/svg>/)![0].matchAll(/<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)"/g)].map(m=>[Number(m[1]),Number(m[2])] as const);
 const en=rects(render('en')),ar=rects(render('ar'));
 expect(ar).toHaveLength(en.length);
 en.forEach(([x,w],i)=>expect(ar[i]![0]).toBe(900-x-w));
});

test('sources carry no long dashes',()=>{
 for(const file of ['../src/content.ts','../src/render.ts','../src/app.js','../src/style.css','../build.ts','../README.md'])
  expect(readFileSync(new URL(file,import.meta.url),'utf8')).not.toMatch(LONG_DASH);
});

test('every stylesheet font is self hosted',()=>{
 const css=readFileSync(new URL('../src/style.css',import.meta.url),'utf8');
 const fonts=[...css.matchAll(/url\((\/assets\/[^)]+\.woff2)\)/g)];
 expect(fonts.length).toBeGreaterThan(0);
 for(const font of fonts)expect(()=>readFileSync(new URL(`../public${font[1]}`,import.meta.url))).not.toThrow();
 expect(css).not.toMatch(/url\((https?:)?\/\//);
});

import {test,expect} from 'bun:test';
import {readFileSync} from 'node:fs';
import {render} from '../src/render';
import {content,rules} from '../src/content';

for(const lang of ['ar','en'] as const){
 const c=content[lang];
 const html=render(lang);

 test(`${lang}: ids are unique and every in-page link lands`,()=>{
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  expect(new Set(ids).size).toBe(ids.length);
  for(const link of html.matchAll(/href="#([^"]+)"/g))expect(ids).toContain(link[1]);
 });

 test(`${lang}: language, direction and the other language are declared`,()=>{
  expect(html).toContain(`lang="${lang}" dir="${c.dir}"`);
  expect(html).toContain(`href="${c.languageUrl}" lang=`);
 });

 test(`${lang}: the data the interactions read agrees with the page`,()=>{
  const data=JSON.parse(html.match(/<script id="page-data" type="application\/json">(.*?)<\/script>/s)![1]!);
  expect(data.steps).toHaveLength(5);
  expect([...html.matchAll(/data-walk="\d+"/g)]).toHaveLength(5);
  expect(data.levels).toHaveLength(4);
  expect(data.recSteps).toHaveLength(4);
  expect([...html.matchAll(/data-rec="\d+"/g)]).toHaveLength(4);
  expect(data.parts).toHaveLength(c.parts.length);
  expect([...html.matchAll(/class="part (own|shared)"/g)]).toHaveLength(c.parts.length);
  expect(data.rules).toEqual(rules);
 });

 test(`${lang}: the environment slider stops at the installation guard and starts consistent`,()=>{
  expect(html).toContain(`max="${rules.maxEnvironments}"`);
  expect(html).toContain(`<b id="n-containers">${rules.systemContainers+2*rules.perEnvironmentContainers}</b>`);
  expect(html).toContain(`<b id="n-memory">${rules.systemMib+2*rules.perEnvironmentMib}</b>`);
 });

 test(`${lang}: honest status is on the page`,()=>{
  expect(html).toContain(c.heroHonest);
  expect(html).toContain(c.proofNote);
  expect(html).toContain(c.recLimit);
  expect(c.milestones.filter(([,,s])=>s==='done')).toHaveLength(1);
 });

 test(`${lang}: no long dashes and no external resources`,()=>{
  expect(html).not.toMatch(/[\u2013\u2014]/);
  expect(html).not.toMatch(/<script[^>]+src="https?:|<link rel="(stylesheet|preload)"[^>]+href="https?:/);
 });
}

test('the Arabic request figure is the English one mirrored',()=>{
 const x=(html:string)=>Number(html.match(/<circle class="packet" r="9" cx="([\d.]+)"/)![1]);
 expect(x(render('ar'))).toBe(900-x(render('en')));
});

test('the rules match the runtime admission policy',()=>{
 const policy=readFileSync(new URL('../../lab/resource_policy.py',import.meta.url),'utf8');
 expect(policy).toContain('START_RESERVE_MIB = 2560');
 expect(policy).toContain("SYSTEM_ROWS = ('system.db', 'system.storage', 'system.management-auth')");
 const runtime=readFileSync(new URL('../../lab/durable_runtime.py',import.meta.url),'utf8');
 expect(runtime).toContain('> 4:');
});

test('every stylesheet and script font is self hosted',()=>{
 const css=readFileSync(new URL('../src/style.css',import.meta.url),'utf8');
 for(const font of css.matchAll(/url\((\/assets\/[^)]+\.woff2)\)/g))expect(()=>readFileSync(new URL(`../public${font[1]}`,import.meta.url))).not.toThrow();
 expect(readFileSync(new URL('../public/assets/OFL-Marhey.txt',import.meta.url),'utf8')).toContain('Marhey Project Authors');
});

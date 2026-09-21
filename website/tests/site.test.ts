import {test,expect} from 'bun:test';
import {render} from '../src/render';
import {content} from '../src/content';
for(const lang of ['ar','en'] as const){
 test(`${lang}: prerendered navigation, language and interactive data agree`,()=>{
  const html=render(lang);const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  expect(new Set(ids).size).toBe(ids.length);
  for(const link of html.matchAll(/href="#([^"]+)"/g))expect(ids).toContain(link[1]);
  expect(html).toContain(`lang="${lang}" dir="${content[lang].dir}"`);
  expect(html).toContain(`href="${content[lang].languageUrl}" lang=`);
  const data=html.match(/<script id="page-data" type="application\/json">(.*?)<\/script>/s)?.[1];
  expect(data).toBeDefined();const parsed=JSON.parse(data!);
  expect(parsed.stageNotes).toHaveLength(4);expect(parsed.levels).toHaveLength(4);
  expect(parsed.serviceNotes).toHaveLength(4);expect(parsed.recoverStages).toHaveLength(4);
  expect([...html.matchAll(/<button data-service="\d+"/g)]).toHaveLength(content[lang].services.length);
  expect(html).toContain(content[lang].studioNote);
  expect(html).toContain(content[lang].serviceNotes[3]);
  expect(html).toContain('aria-pressed="false"');
  expect(html).not.toMatch(/[\u2013\u2014]/);
 });
}

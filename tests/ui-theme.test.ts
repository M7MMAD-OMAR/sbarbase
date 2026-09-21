import {test,expect} from 'bun:test';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';

const ui=join(import.meta.dir,'..','ui');
const css=readFileSync(join(ui,'style.css'),'utf8');
const html=readFileSync(join(ui,'index.html'),'utf8');
const theme=readFileSync(join(ui,'theme.ts'),'utf8');
const main=readFileSync(join(ui,'main.tsx'),'utf8');

/** The palette blocks are the only place a literal colour may appear. */
function splitPalette(source:string){
 const marker='--art-foot:#8ca896}}';
 const end=source.indexOf(marker);
 if(end<0)return undefined;
 return {palette:source.slice(0,end+marker.length),rules:source.slice(end+marker.length)};
}

test('the console palette covers light, dark and an explicit override',()=>{
 const parts=splitPalette(css);
 expect(parts).toBeDefined();
 const {palette}=parts!;
 expect(palette).toContain('color-scheme:light');
 expect(palette).toContain(':root[data-theme=dark]');
 expect(palette).toContain('@media(prefers-color-scheme:dark)');
 // The media query must not be able to override an explicit light choice.
 expect(palette).toContain(':root:not([data-theme=light])');
 for(const token of ['--page','--surface','--border','--text','--muted','--accent','--dot-ok','--error-bg','--notice-bg','--art-1'])
  expect(palette.split(token+':').length-1).toBeGreaterThanOrEqual(2);
});

test('no rule outside the palette hardcodes a colour',()=>{
 const parts=splitPalette(css);
 expect(parts).toBeDefined();
 const literals=parts!.rules.match(/#[0-9a-fA-F]{3,8}\b/g)??[];
 expect(literals).toEqual([]);
 expect(parts!.rules).toContain('var(--accent)');
});

test('the document declares both schemes and the theme is applied before first paint',()=>{
 expect(html).toContain('<meta name="color-scheme" content="light dark">');
 expect(html).toContain('name="theme-color" content="#fafafa" media="(prefers-color-scheme: light)"');
 expect(html).toContain('name="theme-color" content="#171717" media="(prefers-color-scheme: dark)"');
 // No inline script: the content security policy allows same-origin scripts only.
 expect(html).not.toMatch(/<script(?![^>]*src=)[^>]*>/);
 expect(theme).toContain("export type ThemeMode='system'|'light'|'dark'");
 expect(main).toContain('applyTheme(storedTheme())');
 expect(theme).toContain("delete document.documentElement.dataset.theme");
});
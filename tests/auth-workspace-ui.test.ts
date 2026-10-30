import {test,expect} from 'bun:test';
import {readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';

/** The Authentication workspace's design rules (docs/engineering/plans/2026-10-30-auth-providers-workspace.md,
 * "Owner feedback after v0.9.2"): no bordered boxes, no inline styles (the console's CSP blocks them),
 * brand icons kept as local files with their source recorded, and no network fetch for them. */
const css=readFileSync('ui/auth-workspace.css','utf8');
const sources=readFileSync('ui/assets/providers/SOURCES.md','utf8');

test('the workspace styles draw no borders or outlines around components',()=>{
 for(const [index,line] of css.split('\n').entries()) {
  const code=line.replace(/\/\*.*?\*\//g,'');
  for(const declaration of code.matchAll(/(?:^|[{;\s])((?:border|outline)(?:-[a-z-]+)?)\s*:\s*([^;}]+)/g)) {
   const [,property,value]=declaration;
   // `border-radius` rounds a tone step; a reset to 0 and the keyboard focus ring are the only others.
   const allowed=property==='border-radius'||(property==='border'&&value!.trim()==='0')||(property!.startsWith('outline')&&/focus-visible/.test(code));
   if(!allowed)throw new Error(`auth-workspace.css:${index+1} draws ${property}: ${value}`);
  }
 }
 expect(css).not.toMatch(/box-shadow:\s*(0\s+0\s+0\s+1px|inset)/);
});

test('the workspace uses logical properties so it mirrors right to left',()=>{
 expect(css).not.toMatch(/(?:margin|padding)-(?:left|right)\s*:/);
 expect(css).not.toMatch(/(?:^|[;{\s])(?:left|right)\s*:/m);
 expect(css).not.toMatch(/text-align\s*:\s*(?:left|right)/);
});

test('the sign-in screen sets no inline style and loads nothing from the network',()=>{
 for(const file of ['ui/SignIn.tsx','ui/ProviderIcon.tsx','ui/SmtpPanel.tsx','ui/TemplatesPanel.tsx','ui/EmailsTab.tsx']) {
  const source=readFileSync(file,'utf8');
  expect(source).not.toMatch(/style=\{/);
  expect(source).not.toMatch(/https?:\/\/(?!app\.example|idp\.|api\.example)[^'"`\s]*\.(?:svg|png|jpe?g|webp)/);
 }
});

test('every provider icon is a local file with its source recorded and no external reference',()=>{
 const directory='ui/assets/providers';
 const files=readdirSync(directory).filter(name=>name.endsWith('.svg'));
 expect(files.length).toBeGreaterThanOrEqual(17);
 for(const file of files) {
  const svg=readFileSync(join(directory,file),'utf8');
  expect(svg.startsWith('<svg')).toBe(true);
  expect(svg).toMatch(/fill="#[0-9A-Fa-f]{6}"/);
  expect(svg).not.toMatch(/href=|<script|<image|@import|url\(/);
  expect(sources).toContain(file);
 }
 for(const name of ['azure.svg','workos.svg'])expect(files).not.toContain(name);
 expect(sources).toContain('CC0');
});

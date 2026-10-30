import {expect,test} from 'bun:test';
import {readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
import {ago,bytes,clock,milliseconds,percent,visibleFunctions,type FunctionRow} from '../ui/functions-workspace/format';
import {snippet,projectUrl,LANGUAGES,KEY_PLACEHOLDER} from '../ui/functions-workspace/snippets';
import {lines,tokenize,languageOf} from '../ui/functions-workspace/highlight';
import {treeRows} from '../ui/functions-workspace/tree';
import {bars,line} from '../ui/functions-workspace/chart';

const now=Date.UTC(2026,9,30,12,0,0);
const row=(name:string,created:number,updated:number):FunctionRow=>({name,created_at:created,updated_at:updated,verify_jwt:true,deployments:1,size:1,path:`/e_x/functions/v1/${name}`});

test('times read as the cloud writes them',()=>{
 const at=(ms:number)=>ago(now-ms,now);
 expect(at(10_000)).toBe('just now');expect(at(60_000)).toBe('1 minute ago');expect(at(5*60_000)).toBe('5 minutes ago');
 expect(at(3*3600_000)).toBe('3 hours ago');expect(at(16*86400_000)).toBe('16 days ago');expect(at(35*86400_000)).toBe('a month ago');
 expect(at(100*86400_000)).toBe('3 months ago');expect(at(400*86400_000)).toBe('a year ago');expect(at(800*86400_000)).toBe('2 years ago');
 expect(ago(now+5000,now)).toBe('just now');
 expect(bytes(10)).toBe('10 B');expect(bytes(1536)).toBe('1.5 KiB');expect(bytes(5*1024*1024)).toBe('5 MiB');
 expect(milliseconds(null)).toBe('none');expect(milliseconds(42)).toBe('42 ms');expect(milliseconds(2500)).toBe('2.5 s');
 expect(percent(1,8)).toBe('12.5%');expect(percent(0,0)).toBe('0%');
});

test('the list is searched by name and sorted by name, created or updated, either way',()=>{
 const rows=[row('b-hook',3,9),row('a-mail',5,1),row('c-job',1,5)];
 const names=(query:string,key:'name'|'created'|'updated',desc:boolean)=>visibleFunctions(rows,query,key,desc).map(item=>item.name);
 expect(names('','name',false)).toEqual(['a-mail','b-hook','c-job']);
 expect(names('','name',true)).toEqual(['c-job','b-hook','a-mail']);
 expect(names('','created',false)).toEqual(['c-job','b-hook','a-mail']);
 expect(names('','updated',true)).toEqual(['b-hook','c-job','a-mail']);
 expect(names('  MAIL ','name',false)).toEqual(['a-mail']);
 expect(names('zzz','name',false)).toEqual([]);
 expect(rows.map(item=>item.name)).toEqual(['b-hook','a-mail','c-job']);
});

test('every client example names the function, the project address and the key placeholder, and a real key only when given',()=>{
 const input={origin:'https://console.test',path:'/e_aaaaaaaaaaaaaaaaaaaaaaaa/functions/v1/hello',name:'hello'};
 expect(projectUrl(input.origin,input.path)).toBe('https://console.test/e_aaaaaaaaaaaaaaaaaaaaaaaa');
 for(const {id} of LANGUAGES){
  const text=snippet(id,input);
  expect(text).toContain(KEY_PLACEHOLDER);expect(text).toContain(id==='curl'?input.origin+input.path:'hello');
  expect(snippet(id,{...input,key:'sb_publishable_real'})).toContain('sb_publishable_real');
  expect(snippet(id,{...input,key:'sb_publishable_real'})).not.toContain(KEY_PLACEHOLDER);
 }
 expect(snippet('curl',input)).toContain("-H 'apikey: "+KEY_PLACEHOLDER+"'");
 expect(snippet('javascript',input)).toContain("functions.invoke('hello'");
});

test('the code viewer marks comments, strings, numbers and keywords and keeps every character',()=>{
 const source="// note\nconst a = 'x\\'y' + `t ${1}`; /* two\nlines */ return 42n\n";
 const tokens=tokenize(source);
 expect(tokens.map(token=>token.text).join('')).toBe(source);
 const kinds=Object.fromEntries(tokens.filter(token=>token.kind!=='plain').map(token=>[token.text,token.kind]));
 expect(kinds['// note']).toBe('comment');expect(kinds['const']).toBe('keyword');expect(kinds["'x\\'y'"]).toBe('string');
 expect(kinds['/* two\nlines */']).toBe('comment');expect(kinds['42n']).toBe('number');expect(kinds['return']).toBe('keyword');
 const byLine=lines(source,'code');
 expect(byLine.map(tokensOfLine=>tokensOfLine.map(token=>token.text).join('')).join('\n')).toBe(source.replace(/\n$/,'\n'));
 expect(byLine).toHaveLength(4);
 expect(lines('a\n\nb','plain')).toEqual([[{text:'a',kind:'plain'}],[],[{text:'b',kind:'plain'}]]);
 expect(languageOf('index.ts')).toBe('code');expect(languageOf('deno.json')).toBe('code');expect(languageOf('README.md')).toBe('plain');
 // An unterminated string or comment never loops or throws.
 expect(tokenize("'open").map(token=>token.text).join('')).toBe("'open");
 expect(tokenize('/* open').map(token=>token.text).join('')).toBe('/* open');
});

test('the file tree puts folders first, keeps shared files under ../_shared and nests by depth',()=>{
 const rows=treeRows(['index.ts','lib/b.ts','lib/a.ts','lib/deep/c.ts','../_shared/cors.ts','z.ts']);
 expect(rows.map(item=>`${item.depth}${item.folder?'/':':'}${item.name}`)).toEqual(['0/..','1/_shared','2:cors.ts','0/lib','1/deep','2:c.ts','1:a.ts','1:b.ts','0:index.ts','0:z.ts']);
 expect(rows.find(item=>item.name==='cors.ts')!.path).toBe('../_shared/cors.ts');
 expect(treeRows([])).toEqual([]);
});

test('chart geometry scales to the largest value, shares a scale and leaves gaps for missing points',()=>{
 const drawn=bars([0,5,10],300,100);
 expect(drawn.map(bar=>bar.height)).toEqual([0,50,100]);expect(drawn[2]!.y).toBe(0);expect(drawn[0]!.x).toBe(0);expect(drawn[1]!.x).toBe(100);
 expect(bars([1],100,100,0.2,10)[0]!.height).toBe(10);
 expect(bars([0.001],100,100,0.2,1000)[0]!.height).toBe(1);
 expect(bars([],100,100)).toEqual([]);
 expect(line([0,10,null,5],300,100)).toEqual(['0,100 100,0','300,50']);
 expect(line([null,null],10,10)).toEqual([]);
});

const files=(folder:string,pattern:RegExp):string[]=>readdirSync(folder,{withFileTypes:true}).flatMap(entry=>entry.isDirectory()?files(join(folder,entry.name),pattern):pattern.test(entry.name)?[join(folder,entry.name)]:[]);
const sources=[...files('ui/functions-workspace',/\.tsx?$/),'ui/FunctionsWorkspace.tsx'];

test('the workspace draws no borders, uses no inline style, no browser select and no outside address',()=>{
 const css=readFileSync('ui/functions-workspace.css','utf8');
 for(const [,value] of css.matchAll(/(?<![-\w])border(?:-(?:top|bottom|left|right|inline|block)[\w-]*)?\s*:\s*([^;}]+)/g))expect(value!.trim()).toMatch(/^(0|none)$/);
 expect(css).not.toMatch(/outline-style|box-shadow:\s*0\s+0\s+0\s+1px/);
 expect(css).not.toMatch(/https?:\/\//);expect(css).not.toMatch(/@import|url\(/);
 // Logical properties only: no physical left or right offsets for layout.
 expect(css.replace(/direction:ltr;text-align:left/g,'')).not.toMatch(/(?:margin|padding)-(?:left|right)\s*:|(?<![-\w])(?:left|right)\s*:/);
 for(const file of sources){
  const text=readFileSync(file,'utf8');
  expect(text,file).not.toMatch(/\sstyle=\{/);expect(text,file).not.toMatch(/<select[\s>]/);expect(text,file).not.toMatch(/<aside[\s>]/);
  expect(text,file).not.toMatch(/https?:\/\/(?!127\.0\.0\.1)/);expect(text,file).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
 }
});

test('every control the workspace renders is a button, link or field with an accessible name',()=>{
 for(const file of sources.filter(file=>file.endsWith('.tsx'))){
  const text=readFileSync(file,'utf8');
  // An icon only button carries an aria-label (its text is the icon).
  for(const match of text.matchAll(/<button\b[^>]*className="fw-icon"[^>]*>/g))expect(match[0],file).toMatch(/aria-label=/);
  // No button submits a form by accident: each one says type="button", or type="submit" inside a form it belongs to.
  expect((text.match(/<button\b/g)??[]).length,file).toBe((text.match(/type="(?:button|submit)"/g)??[]).length);
 }
});

test('the clock is 12 hour with Western digits',()=>{
 const at=(h:number,m:number,s:number)=>clock(new Date(2026,9,30,h,m,s).getTime());
 expect(at(0,5,9)).toBe('12:05:09 AM');expect(at(12,0,0)).toBe('12:00:00 PM');expect(at(19,8,5)).toBe('7:08:05 PM');expect(at(11,59,59)).toBe('11:59:59 AM');
});

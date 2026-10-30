import {expect,test} from 'bun:test';
import {enter,lineCount,tab} from '../ui/functions-workspace/editing';
import {looksLikeEnv,parseEnv} from '../ui/functions-workspace/env';
import {buildDeploy,checkText,fileNameError,FUNCTION_NAME,rename} from '../ui/functions-workspace/deploy';
import {TEMPLATES} from '../ui/functions-workspace/templates';
import {deployment} from '../src/control/functions';

test('Tab indents the caret, or every line of a selection, and Shift+Tab takes the indent back',()=>{
 expect(tab('ab',1,1,false)).toEqual({value:'a  b',start:3,end:3});
 expect(tab('one\ntwo\nthree',0,13,false)).toEqual({value:'  one\n  two\n  three',start:2,end:19});
 expect(tab('  one\n  two',0,11,true)).toEqual({value:'one\ntwo',start:0,end:7});
 expect(tab(' one',0,4,true).value).toBe('one');
 expect(tab('one',0,3,true).value).toBe('one');
 // Tab over selected text indents its line and never replaces the text.
 expect(tab('abc',1,2,false).value).toBe('  abc');
 // A selection that ends right after a line break does not indent the empty line after it.
 expect(tab('a\nb\n',0,4,false).value).toBe('  a\n  b\n');
});

test('Enter keeps the indentation, adds a level after an opening bracket and splits a pair of brackets',()=>{
 expect(enter('  foo',5,5)).toEqual({value:'  foo\n  ',start:8,end:8});
 expect(enter('if (x) {',8,8)).toEqual({value:'if (x) {\n  ',start:11,end:11});
 expect(enter('  if (x) {}',10,10)).toEqual({value:'  if (x) {\n    \n  }',start:15,end:15});
 expect(enter('a[]',2,2).value).toBe('a[\n  \n]');
 expect(enter('abc',1,2)).toEqual({value:'a\nc',start:2,end:2});
 expect(lineCount('')).toBe(1);expect(lineCount('a\nb\n')).toBe(3);
});

test('pasted .env text becomes secrets: comments, export, quotes, multi line values, repeats',()=>{
 const text=`# keys
STRIPE_KEY=sk_live_abc
export API_URL = https://example.com/path # comment
EMPTY=
SINGLE='it is a # not comment'
DOUBLE="line one\\nline two \\"quoted\\""
PEM="-----BEGIN KEY-----
MIIabc
-----END KEY-----"
STRIPE_KEY=sk_live_last
not a pair
9BAD=x
`;
 expect(parseEnv(text)).toEqual([
  {name:'STRIPE_KEY',value:'sk_live_last'},{name:'API_URL',value:'https://example.com/path'},{name:'EMPTY',value:''},
  {name:'SINGLE',value:'it is a # not comment'},{name:'DOUBLE',value:'line one\nline two "quoted"'},
  {name:'PEM',value:'-----BEGIN KEY-----\nMIIabc\n-----END KEY-----'}]);
 expect(parseEnv('A=1\r\nB=2\r\n')).toEqual([{name:'A',value:'1'},{name:'B',value:'2'}]);
 expect(parseEnv('')).toEqual([]);expect(parseEnv('just words')).toEqual([]);
 expect(parseEnv('OPEN="never closed\nsecond line')).toEqual([{name:'OPEN',value:'never closed\nsecond line'}]);
 expect(looksLikeEnv('A=1')).toBe(true);expect(looksLikeEnv('one\ntwo')).toBe(true);expect(looksLikeEnv('plainvalue')).toBe(false);expect(looksLikeEnv('sk_live_abc')).toBe(false);
});

test('a file name is checked as the server checks it, and shared files start with ../_shared/',()=>{
 expect(fileNameError('lib/util.ts',[])).toBe('');
 expect(fileNameError('../_shared/cors.ts',[])).toBe('');
 for(const bad of ['','  ','../x.ts','/abs.ts','a//b.ts','a/../b.ts','.hidden','a b.ts','a\\b.ts','x'.repeat(201),'../_shared/../x.ts','../_shared/','../other/x.ts'])expect(fileNameError(bad,[])).not.toBe('');
 expect(fileNameError('index.ts',['index.ts'])).toBe('A file with that name exists.');
 expect(FUNCTION_NAME.test('hello-world')).toBe(true);expect(FUNCTION_NAME.test('_x')).toBe(false);expect(FUNCTION_NAME.test('a'.repeat(65))).toBe(false);
 expect(rename({'a.ts':'1','b.ts':'2'},'a.ts','c.ts')).toEqual({'c.ts':'1','b.ts':'2'});
});

test('the deploy request splits own and shared files and refuses what the server would refuse',()=>{
 const built=buildDeploy({'index.ts':'i','lib/a.ts':'a','../_shared/cors.ts':'c'},false);
 expect(built).toEqual({ok:true,body:{files:{'index.ts':'i','lib/a.ts':'a'},shared:{'cors.ts':'c'},verify_jwt:false}});
 expect(buildDeploy({'lib/a.ts':'a'},true).ok).toBe(false);
 expect(buildDeploy({'index.js':'x'},true).ok).toBe(true);
 expect(buildDeploy({'../_shared/index.ts':'x'},true).ok).toBe(false);
 expect(buildDeploy({'index.ts':'x','bad name.ts':'y'},true).ok).toBe(false);
 // What the editor builds is what the server's own check accepts.
 if(built.ok)expect(deployment(built.body).verify_jwt).toBe(false);
});

test('every template is a deployable project the server accepts, with a valid shared file name',()=>{
 expect(TEMPLATES.map(item=>item.id)).toEqual(['hello','webhook','supabase-js','cors']);
 for(const template of TEMPLATES){
  const built=buildDeploy(template.files,template.verify_jwt);
  expect(built.ok,template.id).toBe(true);
  if(built.ok)expect(()=>deployment(built.body),template.id).not.toThrow();
  for(const path of Object.keys(template.files))expect(fileNameError(path,[]),path).toBe('');
  expect(template.files['index.ts']).toContain('Deno.serve');
 }
 expect(TEMPLATES.find(item=>item.id==='webhook')!.verify_jwt).toBe(false);
 expect(Object.keys(TEMPLATES.find(item=>item.id==='cors')!.files)).toContain('../_shared/cors.ts');
 expect(JSON.stringify(TEMPLATES)).not.toMatch(/sk_live|eyJ[A-Za-z0-9]{10}/);
});

test('the start check is described in words for each answer the deploy can give',()=>{
 expect(checkText('passed')).toContain('started the new version');
 expect(checkText('unreachable')).toContain('not checked');
 expect(checkText('skipped')).toContain('not running');
 expect(checkText(undefined)).toContain('not running');
});

test('indenting a selection and taking the indent back returns the text, and the selection stays on the same lines',()=>{
 for(const text of ['one\ntwo\nthree','  a\n    b\n  c','x\n\ny','a']){
  const indented=tab(text,0,text.length,false);
  const back=tab(indented.value,indented.start,indented.end,true);
  expect(back.value).toBe(text);
  expect(indented.value.split('\n')).toHaveLength(text.split('\n').length);
  expect(indented.value.length).toBeGreaterThanOrEqual(text.length);
 }
});

test('a file cannot be named like a folder that holds another file, nor sit inside a file',()=>{
 expect(fileNameError('lib',['lib/util.ts'])).toBe('That name is also a folder of another file.');
 expect(fileNameError('lib/util.ts/x.ts',['lib/util.ts'])).toBe('That name is also a folder of another file.');
 expect(fileNameError('lib/other.ts',['lib/util.ts'])).toBe('');
 expect(fileNameError('library.ts',['lib/util.ts'])).toBe('');
 expect(buildDeploy({'index.ts':'x','lib':'a','lib/util.ts':'b'},true)).toEqual({ok:false,reason:'lib is both a file and a folder.'});
 expect(buildDeploy({'index.ts':'x','../_shared/a':'a','../_shared/a/b.ts':'b'},true).ok).toBe(false);
 expect(buildDeploy({'index.ts':'x','lib/a.ts':'a','lib/b.ts':'b'},true).ok).toBe(true);
 // A file and a shared file of the same name are different places.
 expect(buildDeploy({'index.ts':'x','cors.ts':'a','../_shared/cors.ts':'b'},true).ok).toBe(true);
});

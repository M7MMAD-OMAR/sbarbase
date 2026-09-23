import {test,expect} from 'bun:test';
import {mkdtempSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {readJsonCached} from '../src/http/cached-json';

test('a cached JSON file is reread after an in-place write or a replacing rename',()=>{
 const directory=mkdtempSync(join(tmpdir(),'sbarbase-cache-'));
 try {
  const path=join(directory,'state.json');
  writeFileSync(path,'{"v":1}');
  const first=readJsonCached(path);
  expect(first).toEqual({v:1});
  expect(readJsonCached(path)).toBe(first);
  // Same size, rewritten in place: the file identity still changes.
  writeFileSync(path,'{"v":2}');
  expect(readJsonCached(path)).toEqual({v:2});
  writeFileSync(join(directory,'next.json'),'{"v":3}');
  renameSync(join(directory,'next.json'),path);
  expect(readJsonCached(path)).toEqual({v:3});
  rmSync(path);
  expect(()=>readJsonCached(path)).toThrow();
 } finally {rmSync(directory,{recursive:true,force:true});}
});

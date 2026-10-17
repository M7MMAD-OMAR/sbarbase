import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,rmSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {KeyStore} from '../src/control/keys';

test('a secret key persists without raw material, a publishable key is kept to show again, isolated revocation survives reopen',()=>{
 const dir=mkdtempSync(join(tmpdir(),'sbarbase-keys-')); const path=join(dir,'keys.sqlite');
 let store=new KeyStore(path);
 try {
  const a=store.issue('a_prod'), b=store.issue('b_prod','secret');
  expect(store.resolve('a_prod',a.token)).toBe('publishable');
  expect(store.resolve('b_prod',a.token)).toBeNull();
  expect(store.resolve('b_prod',b.token)).toBe('secret');
  expect(store.revoke('b_prod',a.id)).toBe(false);
  // Publishable keys are public by design and can be shown again; secret keys never.
  expect(store.list('a_prod')[0]!.token).toBe(a.token);
  expect(JSON.stringify(store.list('b_prod'))).not.toContain(b.token);
  store.close();store=new KeyStore(path);
  expect(store.resolve('a_prod',a.token)).toBe('publishable');
  expect(readFileSync(path).includes(Buffer.from(b.token))).toBe(false);
  expect(statSync(path).mode & 0o777).toBe(0o600);
  expect(store.revoke('a_prod',a.id)).toBe(true);
  store.close();store=new KeyStore(path);
  expect(store.resolve('a_prod',a.token)).toBeNull();
  expect(store.resolve('b_prod',b.token)).toBe('secret');
 } finally {store.close();rmSync(dir,{recursive:true});}
});

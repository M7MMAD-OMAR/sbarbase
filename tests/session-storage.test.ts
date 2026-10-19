import {describe,test,expect} from 'bun:test';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {createSessionStorage,SESSION_STORAGE_KEY,startingSession} from '../ui/session-storage';
import {consoleAccess,sessionHasMfa} from '../ui/mfa-state';

const ui=join(import.meta.dir,'..','ui');

function fakeStorage(){
 const data=new Map<string,string>();
 return {data,getItem:(key:string)=>data.get(key)??null,setItem:(key:string,value:string)=>{data.set(key,value);},removeItem:(key:string)=>{data.delete(key);}};
}
const token=(aal:string)=>'h.'+Buffer.from(JSON.stringify({aal})).toString('base64url')+'.s';

test('a session written to the backing store is read back and removed on sign out',()=>{
 const backing=fakeStorage(),storage=createSessionStorage(backing);
 expect(storage.getItem(SESSION_STORAGE_KEY)).toBeNull();
 storage.setItem(SESSION_STORAGE_KEY,'{"a":1}');
 expect(backing.data.get(SESSION_STORAGE_KEY)).toBe('{"a":1}');
 expect(createSessionStorage(backing).getItem(SESSION_STORAGE_KEY)).toBe('{"a":1}');
 storage.removeItem(SESSION_STORAGE_KEY);
 expect(backing.data.has(SESSION_STORAGE_KEY)).toBe(false);
 expect(storage.getItem(SESSION_STORAGE_KEY)).toBeNull();
});

test('a browser that refuses storage falls back to memory instead of breaking sign in',()=>{
 const refusing={getItem:()=>{throw new Error('blocked');},setItem:()=>{throw new Error('blocked');},removeItem:()=>{throw new Error('blocked');}};
 const storage=createSessionStorage(refusing);
 storage.setItem('k','v');
 expect(storage.getItem('k')).toBe('v');
 storage.removeItem('k');
 expect(storage.getItem('k')).toBeNull();
});

test('storage that starts working and then fails keeps the value written after the failure',()=>{
 let broken=false;const backing=fakeStorage();
 const flaky={getItem:(key:string)=>{if(broken)throw new Error('quota');return backing.getItem(key);},
  setItem:(key:string,value:string)=>{if(broken)throw new Error('quota');backing.setItem(key,value);},removeItem:(key:string)=>backing.removeItem(key)};
 const storage=createSessionStorage(flaky);
 storage.setItem('a','1');expect(storage.getItem('a')).toBe('1');
 broken=true;storage.setItem('b','2');expect(storage.getItem('b')).toBe('2');
});

test('a session that cannot be removed is blanked and never read back after sign out',()=>{
 const backing=fakeStorage();
 const stubborn={getItem:backing.getItem,setItem:backing.setItem,removeItem:()=>{throw new Error('refused');}};
 const storage=createSessionStorage(stubborn);
 storage.setItem(SESSION_STORAGE_KEY,'secret-session');
 storage.removeItem(SESSION_STORAGE_KEY);
 expect(storage.getItem(SESSION_STORAGE_KEY)).toBeNull();
 expect(backing.data.get(SESSION_STORAGE_KEY)).toBe('');
 expect(createSessionStorage(backing).getItem(SESSION_STORAGE_KEY)).toBeNull();
});

test('a browser that refuses both removal and overwrite still reads the key as absent on this page',()=>{
 const sticky=new Map([['k','kept']]);
 const refusing={getItem:(key:string)=>sticky.get(key)??null,setItem:()=>{throw new Error('refused');},removeItem:()=>{throw new Error('refused');}};
 const storage=createSessionStorage(refusing);
 expect(storage.getItem('k')).toBe('kept');
 storage.removeItem('k');
 expect(storage.getItem('k')).toBeNull();
 storage.setItem('k','new');
 expect(storage.getItem('k')).toBe('new');
});

test('the console opens only for a session whose token passed MFA',()=>{
 expect(consoleAccess(null)).toBe(false);
 expect(consoleAccess(undefined)).toBe(false);
 expect(consoleAccess({access_token:token('aal1')})).toBe(false);
 expect(consoleAccess({access_token:'not-a-token'})).toBe(false);
 expect(consoleAccess({access_token:token('aal2')})).toBe(true);
 expect(sessionHasMfa(token('aal2'))).toBe(true);
});

test('the client persists its session and the console no longer keeps verification in memory',()=>{
 const api=readFileSync(join(ui,'api.ts'),'utf8'),app=readFileSync(join(ui,'App.tsx'),'utf8');
 expect(api).toContain('persistSession:true');
 expect(api).toContain('storage:createSessionStorage()');
 expect(api).toContain('storageKey:SESSION_STORAGE_KEY');
 expect(api).not.toContain('persistSession:false');
 expect(app).not.toContain('setMfaVerified');
 expect(app).not.toContain("event==='SIGNED_IN'");
 expect(app).toContain('managementPolicy(undefined,signal)');
 expect(app).toContain('policy.data?.authorized');
});

describe('startingSession', () => {
 const backing=(value:string|null)=>({getItem:()=>value});
 test('nothing stored shows the sign-in page at once', () => {
  expect(startingSession(backing(null))).toBe('signed-out');
  expect(startingSession(createSessionStorage(fakeStorage()))).toBe('signed-out');
 });
 test('a stored sign-in is checked before anything shows', () => {
  const storage=createSessionStorage(fakeStorage());
  storage.setItem(SESSION_STORAGE_KEY,'{"access_token":"x"}');
  expect(startingSession(storage)).toBe('checking');
 });
 test('a signed-out key or a storage that throws reads as signed out', () => {
  const storage=createSessionStorage(fakeStorage());
  storage.setItem(SESSION_STORAGE_KEY,'{}');storage.removeItem(SESSION_STORAGE_KEY);
  expect(startingSession(storage)).toBe('signed-out');
  expect(startingSession({getItem:()=>{throw new Error('blocked');}})).toBe('signed-out');
 });
});

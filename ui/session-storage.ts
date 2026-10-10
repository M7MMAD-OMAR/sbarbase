/** Where the console keeps the management sign-in between visits. */
export const SESSION_STORAGE_KEY='sbarbase.management.session';

type Backing=Pick<Storage,'getItem'|'setItem'|'removeItem'>;

/**
 * The Auth client's storage. It uses the browser's localStorage so a reload, a new tab or a closed browser keeps
 * the sign-in for as long as the server accepts it. When the browser refuses storage (private mode, blocked site
 * data) it falls back to memory, so the console still works and the sign-in lasts only until the page closes.
 * The server independently checks every request, so a stored session never grants anything by itself.
 *
 * Signing out must not leave a session behind: a key that could not be removed is overwritten with an empty
 * value, an empty value reads as absent, and the key stays absent for this page even if the browser refuses both.
 */
export function createSessionStorage(backing?:Backing):Backing {
 const memory=new Map<string,string>();
 const removed=new Set<string>();
 let usable:Backing|undefined;
 try{usable=backing??globalThis.localStorage;}catch{usable=undefined;}
 const guarded=<T>(work:(storage:Backing)=>T,fallback:()=>T):T=>{
  if(!usable)return fallback();
  try{return work(usable);}catch{usable=undefined;return fallback();}
 };
 return {
  getItem:key=>{
   if(removed.has(key))return null;
   const value=guarded(storage=>storage.getItem(key),()=>memory.get(key)??null);
   return value===''?null:value;
  },
  setItem:(key,value)=>{
   removed.delete(key);
   guarded(storage=>storage.setItem(key,value),()=>{memory.set(key,value);});
  },
  removeItem:key=>{
   removed.add(key);memory.delete(key);
   if(!usable)return;
   try{usable.removeItem(key);}catch{try{usable.setItem(key,'');}catch{usable=undefined;}}
  }
 };
}

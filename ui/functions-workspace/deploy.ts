/** What the editor sends: the open files as a deploy request, after the same checks the server makes. */
export const FILE_PATH=/^[A-Za-z0-9_][A-Za-z0-9_.@+-]*(\/[A-Za-z0-9_][A-Za-z0-9_.@+-]*)*$/;
export const FUNCTION_NAME=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
export const SHARED='../_shared/';
const ENTRY=['index.ts','index.js'];
export const MAX_FILES=500;

/** True when `name` is a folder that holds another file, or sits inside a file's name: `lib` next to `lib/util.ts`. */
export function collides(name:string,others:string[]):boolean {
 return others.some(other=>other!==name&&(other.startsWith(name+'/')||name.startsWith(other+'/')));
}

/** The problem with a file name as typed, or '' when it is fine. A shared file is written `../_shared/name.ts`. */
export function fileNameError(name:string,existing:string[]):string {
 const value=name.trim();
 if(!value)return 'Enter a file name.';
 const inner=value.startsWith(SHARED)?value.slice(SHARED.length):value;
 if(value.length>200||!FILE_PATH.test(inner)||inner.split('/').some(part=>part==='.'||part==='..'))
  return 'Use letters, digits, . _ - @ + and / between folders. Shared files start with ../_shared/.';
 if(existing.includes(value))return 'A file with that name exists.';
 if(collides(value,existing))return 'That name is also a folder of another file.';
 return '';
}

export type Built={ok:true;body:{files:Record<string,string>;shared:Record<string,string>;verify_jwt:boolean}}|{ok:false;reason:string};
/** Own files and shared files (`../_shared/x` becomes `x` under `shared`), with the function's JWT check, or why it cannot be sent. */
export function buildDeploy(files:Record<string,string>,verify_jwt:boolean):Built {
 const own:Record<string,string>={},shared:Record<string,string>={};
 for(const [path,content] of Object.entries(files)){
  if(path.startsWith(SHARED))shared[path.slice(SHARED.length)]=content;else own[path]=content;
 }
 if(!ENTRY.some(name=>name in own))return {ok:false,reason:'A function needs an index.ts (or index.js) file.'};
 if(Object.keys(own).length+Object.keys(shared).length>MAX_FILES)return {ok:false,reason:'At most 500 files.'};
 for(const path of [...Object.keys(own),...Object.keys(shared)])if(!FILE_PATH.test(path))return {ok:false,reason:`The file name ${path.slice(0,60)} is not allowed.`};
 for(const group of [Object.keys(own),Object.keys(shared)])for(const path of group)if(collides(path,group))return {ok:false,reason:`${path.slice(0,60)} is both a file and a folder.`};
 return {ok:true,body:{files:own,shared,verify_jwt}};
}

/** A file renamed: the same content under a new name; the old name is gone. */
export function rename(files:Record<string,string>,from:string,to:string):Record<string,string> {
 const next:Record<string,string>={};
 for(const [path,content] of Object.entries(files))next[path===from?to:path]=content;
 return next;
}

/** What the deploy answer says about the start check, in words. */
export function checkText(check:string|undefined):string {
 return check==='passed'?'The runtime started the new version before it went live.'
  :check==='unreachable'?'The runtime did not answer the start check, so this version was not checked.'
  :'Edge Functions were not running, so this version was not checked. It starts with the next call.';
}

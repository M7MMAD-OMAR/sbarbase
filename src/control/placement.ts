/** Trusted runtime addresses only. API keys and signing secrets stay outside placement. */
export type RuntimePlacement={auth:string;rest:string;storage?:{url:string;tenantHost:string}};
export type RuntimeRouting={revision:number;maintenance:boolean;placement:RuntimePlacement|null};

export function validatePlacement(input:RuntimePlacement):RuntimePlacement {
 if(!input||typeof input!=='object'||Object.keys(input).some(key=>!['auth','rest','storage'].includes(key)))throw new Error('Invalid placement');
 const endpoint=(value:string)=>{
  const url=new URL(value);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||url.pathname!=='/')throw new Error('Invalid placement endpoint');
  return url.origin;
 };
 const result:RuntimePlacement={auth:endpoint(input.auth),rest:endpoint(input.rest)};
 if(input.storage!==undefined){
  if(!input.storage||Object.keys(input.storage).some(key=>!['url','tenantHost'].includes(key))||
   typeof input.storage.tenantHost!=='string'||!/^[a-z0-9_][a-z0-9_.-]{0,252}$/.test(input.storage.tenantHost))throw new Error('Invalid Storage placement');
  result.storage={url:endpoint(input.storage.url),tenantHost:input.storage.tenantHost};
 }
 return result;
}

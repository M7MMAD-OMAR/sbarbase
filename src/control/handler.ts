import {Catalog} from './catalog';
import {KeyStore} from './keys';
import {managementHandler} from './http';
import {keyHandler} from './key-http';
import type {ManagementIdentity} from './auth';

export function controlHandler(catalog:Catalog,keys:KeyStore,identity:ManagementIdentity) {
 const metadata=managementHandler(catalog,identity),credentials=keyHandler(catalog,keys,identity);
 return (request:Request)=>{
  const path=new URL(request.url).pathname;
  return /\/environments\/[^/]+\/(keys|connection)(\/|$)/.test(path)?credentials(request):metadata(request);
 };
}

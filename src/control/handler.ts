import {Catalog} from './catalog';
import {KeyStore} from './keys';
import {managementHandler} from './http';
import {keyHandler,type ServiceDiscovery} from './key-http';
import type {ManagementIdentity} from './auth';
import {studioHandler} from './studio';

export function controlHandler(catalog:Catalog,keys:KeyStore,identity:ManagementIdentity,services?:ServiceDiscovery,
 studioKey?:()=>Buffer) {
 const metadata=managementHandler(catalog,identity),credentials=keyHandler(catalog,keys,identity,services);
 const studio=studioKey?studioHandler(catalog,identity,studioKey):undefined;
 return (request:Request)=>{
  const path=new URL(request.url).pathname;
  if(studio&&/\/environments\/[^/]+\/studio(\/|$)/.test(path))return studio(request);
  return /\/environments\/[^/]+\/(keys|connection)(\/|$)/.test(path)?credentials(request):metadata(request);
 };
}

import {Catalog} from './catalog';
import {KeyStore} from './keys';
import {managementHandler} from './http';
import {keyHandler,type ServiceDiscovery} from './key-http';
import type {ManagementIdentity} from './auth';
import {studioHandler} from './studio';
import {signInHandler} from './sign-in';
import {shareHandler} from './share';

export function controlHandler(catalog:Catalog,keys:KeyStore,identity:ManagementIdentity,services?:ServiceDiscovery,
 studioKey?:()=>Buffer) {
 const metadata=managementHandler(catalog,identity),credentials=keyHandler(catalog,keys,identity,services);
 const studio=studioKey?studioHandler(catalog,identity,studioKey):undefined,signIn=signInHandler(catalog,identity);
 const share=shareHandler(catalog,identity);
 return (request:Request)=>{
  const path=new URL(request.url).pathname;
  if(studio&&/\/environments\/[^/]+\/studio(\/|$)/.test(path))return studio(request);
  if(/\/environments\/[^/]+\/sign-in$/.test(path))return signIn(request);
  if(/\/environments\/[^/]+\/share$/.test(path))return share(request);
  return /\/environments\/[^/]+\/(keys|connection)(\/|$)/.test(path)?credentials(request):metadata(request);
 };
}

import {Catalog} from './catalog';
import {KeyStore} from './keys';
import {managementHandler} from './http';
import {keyHandler,type ServiceDiscovery} from './key-http';
import type {ManagementIdentity} from './auth';
import {studioHandler} from './studio';
import {signInHandler} from './sign-in';
import {smtpHandler} from './smtp';
import {deploymentSetupHandler} from './deployment-setup';
import {shareHandler} from './share';
import {invitationHandler,type InvitationAccounts} from './invitations';
import {realtimeHandler} from './realtime';
import {scheduledJobsHandler} from './scheduled-jobs';
import {dataApiHandler} from './data-api';
import {observeHandler,type ContainerReader} from './observe';
import {functionsHandler} from './functions';
import type {Boot} from './functions-write';
import type {UsageSource} from './functions-worker';
import {databaseHandler,externalDatabaseHandler} from './database';
import type {DirectDatabase} from '../http/database-proxy';
import {signingHandler} from './signing';
import {RequestLog} from '../gateway/observe';

export function controlHandler(catalog:Catalog,keys:KeyStore,identity:ManagementIdentity,services?:ServiceDiscovery,
 studioKey?:()=>Buffer,requests=new RequestLog(),containers?:ContainerReader,accounts?:InvitationAccounts,direct?:DirectDatabase,functionsBoot?:Boot,functionsUsage?:UsageSource) {
 keys.useRuntimeEpoch(runtime=>catalog.runtimeEpoch(runtime));
 const metadata=managementHandler(catalog,identity,undefined,keys),credentials=keyHandler(catalog,keys,identity,services);
 const studio=studioKey?studioHandler(catalog,identity,studioKey):undefined,signIn=signInHandler(catalog,identity),smtp=smtpHandler(catalog,identity,'.secrets/upstream'),
  realtime=realtimeHandler(catalog,identity),scheduledJobs=scheduledJobsHandler(catalog,identity),observe=observeHandler(catalog,identity,requests,containers),
  functions=functionsHandler(catalog,identity,undefined,undefined,requests,functionsBoot,functionsUsage),database=databaseHandler(catalog,identity,undefined,direct),externalDatabase=externalDatabaseHandler(catalog,identity,direct),
  signing=signingHandler(catalog,identity),dataApi=dataApiHandler(catalog,identity);
 const share=shareHandler(catalog,identity),deploymentSetup=deploymentSetupHandler(catalog,identity);
 const invitations=invitationHandler(catalog,identity,accounts);
 return (request:Request)=>{
  const path=new URL(request.url).pathname;
  if(studio&&/\/environments\/[^/]+\/studio(\/|$)/.test(path))return studio(request);
  if(/\/environments\/[^/]+\/deployment-setup$/.test(path))return deploymentSetup(request);
  if(/\/environments\/[^/]+\/(sign-in|auth-config)$/.test(path))return signIn(request);
  if(/\/environments\/[^/]+\/smtp$/.test(path))return smtp(request);
  if(/\/environments\/[^/]+\/share$/.test(path))return share(request);
  if(path==='/management/invitations/redeem'||/\/organizations\/[^/]+\/invitations(\/|$)/.test(path))return invitations(request);
  if(/\/environments\/[^/]+\/realtime$/.test(path))return realtime(request);
  if(/\/environments\/[^/]+\/scheduled-jobs$/.test(path))return scheduledJobs(request);
  if(/\/environments\/[^/]+\/data-api$/.test(path))return dataApi(request);
  if(/\/environments\/[^/]+\/(metrics|logs)$/.test(path))return observe(request);
  if(/\/environments\/[^/]+\/(functions|function-secrets)(\/|$)/.test(path))return functions(request);
  if(/\/environments\/[^/]+\/database\/external$/.test(path))return externalDatabase(request);
  if(/\/environments\/[^/]+\/database(\/password)?$/.test(path))return database(request);
  if(/\/environments\/[^/]+\/signing-key(\/rotate)?$/.test(path))return signing(request);
  return /\/environments\/[^/]+\/(keys|connection)(\/|$)/.test(path)?credentials(request):metadata(request);
 };
}

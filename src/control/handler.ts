import {Catalog} from './catalog';
import {KeyStore} from './keys';
import {managementHandler} from './http';
import {keyHandler,type ServiceDiscovery} from './key-http';
import type {ManagementIdentity} from './auth';
import {studioHandler} from './studio';
import {signInHandler} from './sign-in';
import {smtpHandler} from './smtp';
import {mailTemplatesHandler} from './mail-templates';
import {smsHandler} from './sms';
import {authAdminHandler,type AuthAdmin} from './auth-admin';
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
import {servicesHandler,installationServicesHandler,type Stats} from './services';
import type {ServiceActivity} from '../gateway/activity';
import type {OnDemandGate} from '../gateway/managed';
export type ServicesOptions={activity?:ServiceActivity;onDemand?:OnDemandGate;stats?:()=>Stats|undefined;memory?:()=>{availableBytes:number|null;totalBytes:number|null};now?:()=>number};

export function controlHandler(catalog:Catalog,keys:KeyStore,identity:ManagementIdentity,services?:ServiceDiscovery,
 studioKey?:()=>Buffer,requests=new RequestLog(),containers?:ContainerReader,accounts?:InvitationAccounts,direct?:DirectDatabase,functionsBoot?:Boot,functionsUsage?:UsageSource,authAdmin?:AuthAdmin,serviceOptions:ServicesOptions={}) {
 keys.useRuntimeEpoch(runtime=>catalog.runtimeEpoch(runtime));
 const metadata=managementHandler(catalog,identity,undefined,keys),credentials=keyHandler(catalog,keys,identity,services);
 const studio=studioKey?studioHandler(catalog,identity,studioKey):undefined,signIn=signInHandler(catalog,identity),smtp=smtpHandler(catalog,identity,'.secrets/upstream'),mailTemplates=mailTemplatesHandler(catalog,identity,'.secrets/upstream'),sms=smsHandler(catalog,identity,'.secrets/upstream'),authAdminRoutes=authAdminHandler(catalog,identity,authAdmin),
  realtime=realtimeHandler(catalog,identity),scheduledJobs=scheduledJobsHandler(catalog,identity),observe=observeHandler(catalog,identity,requests,containers),
  functions=functionsHandler(catalog,identity,undefined,undefined,requests,functionsBoot,functionsUsage,serviceOptions.activity),database=databaseHandler(catalog,identity,undefined,direct),externalDatabase=externalDatabaseHandler(catalog,identity,direct),
  signing=signingHandler(catalog,identity),dataApi=dataApiHandler(catalog,identity),servicesRoute=servicesHandler(catalog,identity,serviceOptions),installationServices=installationServicesHandler(catalog,identity);
 const share=shareHandler(catalog,identity),deploymentSetup=deploymentSetupHandler(catalog,identity);
 const invitations=invitationHandler(catalog,identity,accounts);
 return (request:Request)=>{
  const path=new URL(request.url).pathname;
  if(studio&&/\/environments\/[^/]+\/studio(\/|$)/.test(path))return studio(request);
  if(/\/environments\/[^/]+\/deployment-setup$/.test(path))return deploymentSetup(request);
  if(/\/environments\/[^/]+\/(sign-in|auth-config)$/.test(path))return signIn(request);
  if(/\/environments\/[^/]+\/smtp$/.test(path))return smtp(request);
  if(/\/environments\/[^/]+\/mail-templates$/.test(path))return mailTemplates(request);
  if(/\/environments\/[^/]+\/sms$/.test(path))return sms(request);
  if(/\/environments\/[^/]+\/(auth-audit|sso-providers)(\/[^/]+)?$/.test(path))return authAdminRoutes(request);
  if(/\/environments\/[^/]+\/share$/.test(path))return share(request);
  if(path==='/management/invitations/redeem'||/\/organizations\/[^/]+\/invitations(\/|$)/.test(path))return invitations(request);
  if(path==='/management/v1/installation/services')return installationServices(request);
  if(/\/environments\/[^/]+\/services$/.test(path))return servicesRoute(request);
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

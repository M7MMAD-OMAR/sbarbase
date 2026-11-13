import {hasAgentPermission,type AgentPermission} from './agent-permissions';
import type {AccessToken} from './access-tokens';

export type AgentOperation={name:string;permission:AgentPermission;method:'GET'|'PUT'|'PATCH'|'POST'|'DELETE';target:'project'|'organization';risk:'read'|'write';description:string;route:string};
const operation=(name:string,permission:AgentPermission,method:AgentOperation['method'],route:string,description:string,target:AgentOperation['target']='project'):AgentOperation=>
 ({name,permission,method,route,target,risk:method==='GET'?'read':'write',description});
/** Fixed routes only. There is no arbitrary management URL, account security, minting or purge. */
export const AGENT_OPERATIONS:readonly AgentOperation[]=[
 operation('organization.projects.read','projects.read','GET','/organizations/{organization}/projects','List projects in this organization.','organization'),
 operation('organization.rename','organization.write','PATCH','/organizations/{organization}','Rename this organization. Input: {name}.','organization'),
 operation('organization.members.read','organization.read','GET','/organizations/{organization}/members','List members and their roles.','organization'),
 operation('organization.audit.read','logs.read','GET','/organizations/{organization}/audit','Read this organization activity log.','organization'),
 operation('project.environments.read','projects.read','GET','/projects/{project}/environments','List environments in this project.'),
 operation('project.environments.create','projects.write','POST','/projects/{project}/environments','Create an independent environment. Input: {name}.'),
 operation('project.rename','projects.write','PATCH','/projects/{project}','Rename this project. Input: {name}.'),
 operation('environment.rename','projects.write','PATCH','/environments/{environment}','Rename this environment. Input: {name}.'),
 operation('environment.provision.read','projects.read','GET','/environments/{environment}/provision','Read provisioning status.'),
 operation('environment.services.read','projects.read','GET','/environments/{environment}/services','Read enabled services and operating modes.'),
 operation('auth.settings.read','auth.read','GET','/environments/{environment}/sign-in','Read sign-in settings, revision and redirect allow-list. Provider secrets are masked.'),
 operation('auth.settings.update','auth.write','PUT','/environments/{environment}/sign-in','Save sign-in settings. Input: site_url, redirect_urls, signup, anonymous, session, providers, config and base_revision. Use the current revision.'),
 operation('auth.config.read','auth.read','GET','/environments/{environment}/auth-config','Read supported Auth configuration without signing or provider secrets.'),
 operation('auth.smtp.read','auth.read','GET','/environments/{environment}/smtp','Read SMTP configuration with secrets masked.'),
 operation('auth.smtp.update','auth.write','PUT','/environments/{environment}/smtp','Save SMTP configuration through the same validation as the console.'),
 operation('auth.sms.read','auth.read','GET','/environments/{environment}/sms','Read SMS provider configuration with secrets masked.'),
 operation('auth.sms.update','auth.write','PUT','/environments/{environment}/sms','Save SMS configuration through the same validation as the console.'),
 operation('auth.mail_templates.read','auth.read','GET','/environments/{environment}/mail-templates','Read the saved Auth mail templates.'),
 operation('auth.mail_templates.update','auth.write','PUT','/environments/{environment}/mail-templates','Save Auth mail templates through the same validation as the console.'),
 operation('auth.audit.read','logs.read','GET','/environments/{environment}/auth-audit','Read Auth audit events.'),
 operation('auth.sso.read','auth.read','GET','/environments/{environment}/sso-providers','List SSO identity providers without secrets.'),
 operation('auth.sso.create','auth.write','POST','/environments/{environment}/sso-providers','Create an SSO identity provider. Input is the console SSO provider form.'),
 operation('auth.sso.delete','auth.delete','DELETE','/environments/{environment}/sso-providers/{provider}','Remove an SSO identity provider. Input: {provider_id}.'),
 operation('keys.read','keys.read','GET','/environments/{environment}/keys','List API keys without secret values.'),
 operation('keys.create','keys.write','POST','/environments/{environment}/keys','Create an API key. Input: {type,name}. Secret API keys bypass data restrictions; grant only to trusted agents.'),
 operation('keys.revoke','keys.delete','DELETE','/environments/{environment}/keys/{key}','Revoke an API key immediately. Input: {key_id}.'),
 operation('database.connection.read','database.read','GET','/environments/{environment}/database','Read direct connection state. Does not enable access.'),
 operation('database.connection.update','database.write','PUT','/environments/{environment}/database','Enable or disable direct database access. Input: {enabled}.'),
 operation('database.data_api.read','database.read','GET','/environments/{environment}/data-api','Read exposed schemas, search path and row limit.'),
 operation('database.data_api.update','database.configure','PUT','/environments/{environment}/data-api','Save exposed schemas, search path and row limit. Input: {schemas,extraSearchPath,maxRows}.'),
 operation('database.scheduled_jobs.read','database.read','GET','/environments/{environment}/scheduled-jobs','Read scheduled jobs service state.'),
 operation('database.scheduled_jobs.update','database.configure','PUT','/environments/{environment}/scheduled-jobs','Enable or disable scheduled jobs. Input: {enabled}.'),
 operation('realtime.read','realtime.read','GET','/environments/{environment}/realtime','Read Realtime configuration.'),
 operation('realtime.update','realtime.write','PUT','/environments/{environment}/realtime','Save Realtime configuration through the same validation as the console.'),
 operation('functions.settings.read','functions.read','GET','/environments/{environment}/functions','Read function service state, deployed functions and secret names.'),
 operation('functions.settings.update','functions.write','PUT','/environments/{environment}/functions','Enable or disable the functions service. Input: {enabled}.'),
 operation('functions.secrets.update','functions.write','PUT','/environments/{environment}/function-secrets','Set or remove function secrets. Input: {secrets:{NAME:value}}. Secret values are never included in previews.'),
 operation('logs.read','logs.read','GET','/environments/{environment}/logs','Read request or service logs. Optional input: {source,lines,errors}.'),
 operation('metrics.read','logs.read','GET','/environments/{environment}/metrics','Read request and service metrics.'),
];
export function availableAgentOperations(token:AccessToken){
 return AGENT_OPERATIONS.filter(item=>hasAgentPermission(token,item.permission)&&(token.scope.kind!=='project'||item.target!=='organization'))
  .map(({route,...item})=>item);
}

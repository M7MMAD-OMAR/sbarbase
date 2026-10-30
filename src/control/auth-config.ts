import registryFile from './auth-config-registry.json';
import type {SignInSettings} from './sign-in';

/** The read side of the Authentication workspace (docs/engineering/plans/2026-10-30-auth-providers-workspace.md).
 * One environment's saved sign-in settings, shown under the names the Supabase platform uses,
 * grouped by page like the cloud's. The registry (auth-config-registry.json) says which setting
 * is which; this module only reads it. A secret is never returned: its entry says only whether
 * one is set. An environment with no saved settings shows the pinned defaults. */

type CoreEntry={key:string;page:string;label:string;type:'url'|'list'|'bool'|'seconds';from:string;invert?:boolean;default:unknown;null_text?:string;owner:string};
type ProviderField={suffix:string;type:'bool'|'text'|'secret'|'url'|'callback';from?:string;default?:unknown;only?:string;secret?:boolean;computed?:boolean};
type ProviderEntry={label:string;url:'none'|'optional'|'required'};
type Registry={version:number;pages:{id:string;label:string}[];core:CoreEntry[];provider_fields:ProviderField[];providers:Record<string,ProviderEntry>};
export const REGISTRY=registryFile as unknown as Registry;

/** `invert`: the value is the platform key's own (DISABLE_SIGNUP), the label asks the positive question, so the console shows the opposite.
 * `inactive`: saved, but Auth receives nothing for it while its provider is disabled. `null_text`: what an empty value means. */
export type ConfigField={key:string;label:string;type:string;value?:unknown;secret_set?:boolean;from_default:boolean;invert?:boolean;inactive?:boolean;null_text?:string};
export type ConfigPage={id:string;label:string;fields:ConfigField[]};
export type ProviderView={id:string;label:string;url:'none'|'optional'|'required';fields:ConfigField[]};
export type AuthConfigView={configured:boolean;callback_url:string;pages:ConfigPage[];providers:ProviderView[]};

function pick(settings:SignInSettings|null,path:string):unknown {
 if(!settings)return undefined;
 let value:unknown=settings;
 for(const part of path.split('.')){
  if(!value||typeof value!=='object')return undefined;
  value=(value as Record<string,unknown>)[part];
 }
 return value;
}

/** What an environment shows: the saved value, or the pinned default marked as such. */
function core(entry:CoreEntry,settings:SignInSettings|null):ConfigField {
 let value=pick(settings,entry.from);
 const saved=value!==undefined&&value!==null;
 // DISABLE_SIGNUP is stored as `signup`, the positive flag: the platform key's value is its opposite.
 if(saved&&entry.invert)value=!value;
 if(!saved)value=entry.default;
 return {key:entry.key,label:entry.label,type:entry.type,value,from_default:!saved,...(entry.invert?{invert:true}:{}),
  ...(entry.null_text?{null_text:entry.null_text}:{})};
}

function providerFields(name:string,settings:SignInSettings|null,callback:string):ConfigField[] {
 const meta=REGISTRY.providers[name]!,saved=settings?.providers?.[name];
 // lab/auth_settings.py `configuration` passes nothing for a disabled provider.
 const inactive=!!saved&&!saved.enabled;
 const prefix='EXTERNAL_'+name.toUpperCase()+'_';
 const fields:ConfigField[]=[];
 for(const field of REGISTRY.provider_fields) {
  if(field.only==='url'&&meta.url==='none')continue;
  const key=prefix+field.suffix,label=field.suffix.toLowerCase().replace(/_/g,' ');
  if(field.type==='callback'){fields.push({key,label,type:'callback',value:callback,from_default:false});continue;}
  if(field.secret){fields.push({key,label,type:'secret',secret_set:!!saved?.secret,from_default:!saved,...(inactive?{inactive:true}:{})});continue;}
  const value=saved?(saved as unknown as Record<string,unknown>)[field.from!]:undefined;
  fields.push({key,label,type:field.type,value:value??field.default,from_default:!saved,...(inactive&&field.suffix!=='ENABLED'?{inactive:true}:{})});
 }
 return fields;
}

/** The environment's Authentication configuration as the console shows it. `runtime` names the
 * environment's own Auth address under `publicAddress` (the installation's public URL); nothing here reads another environment's settings. */
export function authConfigView(settings:SignInSettings|null,runtime:string,publicAddress:string):AuthConfigView {
 const callback=`${publicAddress}/${runtime}/auth/v1/callback`;
 const pages=REGISTRY.pages.map(page=>({id:page.id,label:page.label,
  fields:REGISTRY.core.filter(entry=>entry.page===page.id).map(entry=>core(entry,settings))}));
 const providers=Object.keys(REGISTRY.providers).map(id=>({id,label:REGISTRY.providers[id]!.label,url:REGISTRY.providers[id]!.url,
  fields:providerFields(id,settings,callback)}));
 return {configured:settings!==null,callback_url:callback,pages,providers};
}

export type FieldErrors=Record<string,string>;

export function shareError(value:string,ceiling:number):string {
 const share=Number(value);
 return !value.trim()||!Number.isSafeInteger(share)||share<1||share>ceiling
  ?`Enter a whole number from 1 to ${ceiling}.`:'';
}

export function secretErrors(name:string,value:string):FieldErrors {
 const errors:FieldErrors={};
 if(!/^[A-Z_][A-Z0-9_]{0,127}$/.test(name))errors['secret-name']='Use letters, digits and underscores, starting with a letter or underscore, up to 128 characters.';
 else if(name.startsWith('SUPABASE_')||name.startsWith('SB_'))errors['secret-name']='Names starting with SUPABASE_ or SB_ are reserved.';
 if(!value)errors['secret-value']='Enter a secret value.';
 else if(value.length>65536)errors['secret-value']='Use at most 65536 characters.';
 return errors;
}

function webAddressError(value:string):string {
 if(!value||value.length>2048||/[\s\x00-\x1f]/.test(value))return 'Enter an HTTP or HTTPS address, up to 2048 characters, without spaces.';
 try{const url=new URL(value);if(['http:','https:'].includes(url.protocol)&&url.hostname&&!url.username&&!url.password)return '';}catch{}
 return 'Enter an HTTP or HTTPS address without a username or password.';
}

type ProviderInput={enabled:boolean;client_id:string;secret?:string;secret_set?:boolean;url:string};
export function signInErrors(site:string,redirects:string,providers:Record<string,ProviderInput>):FieldErrors {
 const errors:FieldErrors={};
 const siteError=webAddressError(site.trim());if(siteError)errors['site-url']=siteError;
 const addresses=redirects.split('\n').map(line=>line.trim()).filter(Boolean);
 if(addresses.length>50)errors.redirects='Use at most 50 redirect addresses.';
 else if(addresses.some(address=>address.length>2048||/[\s\x00-\x1f,]/.test(address)||!/^[a-z][a-z0-9+.-]*:\/\//.test(address)))
  errors.redirects='Enter one address per line, up to 2048 characters each, with a scheme such as https:// or myapp:// and no spaces or commas.';
 for(const [name,entry] of Object.entries(providers)){
  if(entry.client_id.length>512||/\s/.test(entry.client_id))errors[name+'-client']='Use at most 512 characters without spaces.';
  else if(entry.enabled&&!entry.client_id)errors[name+'-client']='Enter a client ID for this enabled provider.';
  const secret=entry.secret??'';
  if(secret.length>4096||/\s/.test(secret))errors[name+'-secret']='Use at most 4096 characters without spaces.';
  else if(entry.enabled&&!secret&&!entry.secret_set)errors[name+'-secret']='Enter a client secret for this enabled provider.';
  if(entry.url){const error=webAddressError(entry.url);if(error)errors[name+'-url']=error;}
  else if(entry.enabled&&name==='keycloak')errors[name+'-url']='Enter the Keycloak server address.';
 }
 return errors;
}

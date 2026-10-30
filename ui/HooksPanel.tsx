import {Switch} from './AwSwitch';
import type {FieldErrors} from './settings-validation';

type Field={key:string;label:string;type:string;hint?:string;value?:unknown;max?:number};
type Value=boolean|number|string;
const PREFIX='pg-functions://postgres/';
const NAME=/^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

/** Hooks, as the Supabase cloud's Auth Hooks page: each hook is a switch and, when on, a Postgres function in this environment's database that Auth
 * calls. Only Postgres function hooks are offered: an HTTPS hook needs the environment's Auth to reach the internet and a shared secret, which this
 * console does not set up. The two fields become `pg-functions://postgres/<schema>/<function>`. Saved with the page's one save bar. */
export function HooksPanel({fields,cfg,setCfg,busy,errors,authRole,failed,persisted}:{fields:Field[];cfg:Record<string,Value|null>;setCfg:(value:Record<string,Value|null>)=>void;busy:boolean;errors:FieldErrors;authRole:string;failed:string;persisted:Record<string,Value>}){
 if(failed)return <p className="aw-notice" role="alert">These settings could not be loaded. Reload the page to try again. {failed}</p>;
 if(fields.length===0)return <p className="aw-hint">Loading these settings…</p>;
 const switches=fields.filter(field=>field.key.endsWith('_ENABLED'));
 return <>
  <p className="aw-notice" role="note">A hook that is on and names a function that does not exist, or that the Auth login may not run, makes the sign-ups and sign-ins it covers fail. Create the function and grant it first, then turn the hook on.</p>
  <p className="aw-hint aw-lead">The function runs in this environment's database as its Auth login. Create it in the SQL editor, then let that login run it: <code>grant execute on function schema.name to {authRole};</code> and <code>grant usage on schema schema to {authRole};</code></p>
  {switches.map(field=>{
   const base=field.key.slice(0,-'_ENABLED'.length),uriKey=base+'_URI';
   const uri=typeof cfg[uriKey]==='string'?cfg[uriKey] as string:'';
   const rest=uri.startsWith(PREFIX)?uri.slice(PREFIX.length):'';
   const [schema='public',name='']=rest.includes('/')?rest.split('/'):['public',rest];
   const on=typeof cfg[field.key]==='boolean'?cfg[field.key]===true:false;
   const write=(nextSchema:string,nextName:string)=>setCfg({...cfg,[uriKey]:nextSchema===''&&nextName===''?null:`${PREFIX}${nextSchema}/${nextName}`});
   const problem=errors['config-'+uriKey];
   return <div className="aw-group aw-hook" key={field.key}>
    <Switch id={'config-'+field.key} checked={on} disabled={busy} label={field.label} hint={field.hint??''} onChange={value=>{
     // Back to off with nothing saved is no change; a half typed function name is dropped with it, never sent.
     const keep=value||persisted[field.key]!==undefined;
     const half=!value&&persisted[uriKey]===undefined;
     setCfg({...cfg,[field.key]:keep?value:null,...(half?{[uriKey]:null}:{})});
    }}/>
    {on&&<div className="aw-hook-fields">
     <div className="aw-field"><label htmlFor={'hook-schema-'+base}>Schema</label><input id={'hook-schema-'+base} disabled={busy} maxLength={63} value={schema} aria-invalid={Boolean(problem)} onChange={event=>write(event.target.value,name)}/></div>
     <div className="aw-field"><label htmlFor={'hook-name-'+base}>Function</label><input id={'config-'+uriKey} disabled={busy} maxLength={63} value={name} placeholder="my_hook" aria-invalid={Boolean(problem)} aria-describedby={problem?'hook-error-'+base:undefined} onChange={event=>write(schema,event.target.value)}/>
      {problem&&<p id={'hook-error-'+base} className="aw-error" role="alert">{problem}</p>}</div>
    </div>}
   </div>;
  })}
 </>;
}

/** The check the page runs before a save: an enabled hook needs a function with valid names. */
export function hookErrors(fields:Field[],cfg:Record<string,Value|null>):FieldErrors{
 const errors:FieldErrors={};
 for(const field of fields.filter(item=>item.key.endsWith('_ENABLED'))){
  if(cfg[field.key]!==true)continue;
  const uriKey=field.key.slice(0,-'_ENABLED'.length)+'_URI',uri=cfg[uriKey];
  const rest=typeof uri==='string'&&uri.startsWith(PREFIX)?uri.slice(PREFIX.length).split('/'):[];
  if(rest.length!==2||!NAME.test(rest[0]!)||!NAME.test(rest[1]!))errors['config-'+uriKey]='Enter a schema and a function name: letters, digits and underscores, not starting with a digit.';
 }
 return errors;
}

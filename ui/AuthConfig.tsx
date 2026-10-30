import {useData,type Api} from './api';
import {ErrorMessage,Loading} from './components';

type Field={key:string;label:string;type:string;value?:unknown;secret_set?:boolean;from_default:boolean;invert?:boolean;inactive?:boolean;null_text?:string};
type Page={id:string;label:string;fields:Field[]};
type Provider={id:string;label:string;url:'none'|'optional'|'required';fields:Field[]};
type Answer={state:'unconfigured'|'pending'|'applied'|'failed';failure:string|null;
 config:{configured:boolean;callback_url:string;pages:Page[];providers:Provider[]}};

/** A setting's value as the cloud shows it: a switch reads On or Off, a lifetime reads in hours or minutes. */
function shown(field:Field):string {
 if(field.type==='secret')return field.secret_set?'Set (hidden)':'Not set';
 const value=field.value;
 if(field.type==='bool')return (field.invert?!value:value)?'On':'Off';
 if(field.type==='list')return Array.isArray(value)&&value.length?value.join('\n'):'None';
 if(field.type==='seconds')return typeof value==='number'?(value%3600===0?`${value/3600} h`:value%60===0?`${value/60} min`:`${value} s`):field.null_text??'Not set';
 return value===''||value===undefined||value===null?'Not set':String(value);
}

function Rows({fields}:{fields:Field[]}){
 return <dl className="config-rows">{fields.map(field=><div key={field.key} className="config-row">
  <dt>{field.label}<span className="muted small config-key"> {field.key}</span></dt>
  <dd>{shown(field).split('\n').map((line,index)=><div key={index}>{line}</div>)}{field.from_default&&<span className="muted small"> {field.null_text?'':'default'}</span>}{field.inactive&&<span className="muted small"> saved, not used while the provider is off</span>}</dd>
 </div>)}</dl>;
}

/** The environment's Authentication configuration, read only: the same settings the cloud's pages show, under the
 * platform's own key names. Secrets are never sent to the console; a secret shows only whether one is set. Changes
 * are made in Sign-in settings below until the workspace becomes editable (docs/engineering/plans/2026-10-30-auth-providers-workspace.md). */
export function AuthConfigSection({path,request}:{path:string;request:Api}){
 const data=useData<{data:Answer}>(signal=>request(path+'/auth-config','GET',undefined,signal),[path,request]);
 const current=data.data?.data;
 return <section className="details"><h2>Authentication configuration</h2>
 <p className="muted small">What this environment's Auth is set to once saved settings are applied. Secrets are never shown, only whether one is set. Settings of other environments never appear here.</p>
 <ErrorMessage message={data.error}/>{data.loading?<Loading/>:current&&<>
  {(current.state==='pending'||current.state==='failed')&&<p className="notice">{current.state==='pending'?'These values are saved and Auth is applying them. Until it finishes, Auth may still run the previous settings.':'These values are saved but Auth did not start with them, so it still runs the previous settings.'}{current.failure?' '+current.failure:''}</p>}
  {!current.config.configured&&<p className="notice">No sign-in settings are saved yet, so Auth runs on its defaults.</p>}
  {current.config.pages.map(page=><div key={page.id}><h3>{page.label}</h3><Rows fields={page.fields}/></div>)}
  <h3>Auth Providers</h3>
  <p className="small muted">Callback URL for every provider of this environment: <code>{current.config.callback_url}</code></p>
  {current.config.providers.map(provider=>{
   const enabled=provider.fields.find(field=>field.key.endsWith('_ENABLED'))?.value===true;
   return <details key={provider.id} className="config-provider"><summary>{provider.label} <span className={'state '+(enabled?'applied':'')}>{enabled?'Enabled':'Disabled'}</span></summary>
    <Rows fields={provider.fields}/></details>;
  })}
 </>}</section>;
}

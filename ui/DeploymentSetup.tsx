import {useEffect,useRef,useState,type FormEvent} from 'react';
import type {DeploymentIntent,DeploymentPreview,DeploymentSetupView} from '../src/control/deployment-setup';

export type DeploymentSetupClient={load:(signal:AbortSignal)=>Promise<DeploymentSetupView>;
 preview:(intent:DeploymentIntent,signal:AbortSignal)=>Promise<DeploymentPreview>;
 save:(intent:DeploymentIntent,revision:number,signal:AbortSignal)=>Promise<DeploymentSetupView>};

/** Keep conditional requests local to this flow; credentials never enter its form state. */
export function deploymentSetupClient(environment:string,token:()=>string):DeploymentSetupClient{
 if(!/^[a-f0-9-]{36}$/.test(environment))throw new Error('Invalid environment');
 const path='/management/v1/environments/'+environment+'/deployment-setup';
 async function send(method:string,signal:AbortSignal,intent?:DeploymentIntent,revision?:number){
  const response=await fetch(path,{method,signal,headers:{authorization:'Bearer '+token(),
   ...(intent?{'content-type':'application/json'}:{}),...(revision!==undefined?{'if-match':String(revision)}:{})},
   ...(intent?{body:JSON.stringify(intent)}:{})});
  if(!response.ok)throw new Error(response.status===409?'The setup changed. Refresh before saving.':
   response.status===401||response.status===403?'Your management access changed. Verify MFA and refresh.':
   response.status===400?'Check the origin, addresses and private reference names.':'Setup could not be loaded. Try again.');
  return (await response.json()).data;
 }
 return {load:signal=>send('GET',signal),preview:(intent,signal)=>send('POST',signal,intent),
  save:(intent,revision,signal)=>send('PUT',signal,intent,revision)};
}

const initial:DeploymentIntent={version:1,profile:'public',public_url:'',ipv4:[],ipv6:[],provider_access:true,
 tls:'managed',acme_email:'',certificate_ref:null,site_url:'',redirect_urls:[],smtp_ref:null};

/** Installation operators prepare one environment with a shared installation origin. */
export function DeploymentSetup({client,environmentName}:{client:DeploymentSetupClient;environmentName:string}){
 const [intent,setIntent]=useState<DeploymentIntent>(initial),[revision,setRevision]=useState(0),
  [preview,setPreview]=useState<DeploymentPreview|null>(null),[busy,setBusy]=useState(false),
  [error,setError]=useState(''),[status,setStatus]=useState(''),[loaded,setLoaded]=useState(false);
 const [ipv4,setIpv4]=useState(''),[ipv6,setIpv6]=useState(''),[redirects,setRedirects]=useState('');
 const active=useRef<AbortController|null>(null),form=useRef<HTMLFormElement>(null);
 useEffect(()=>{
  const controller=new AbortController();active.current=controller;setLoaded(false);setBusy(false);setError('');setPreview(null);
  client.load(controller.signal).then(view=>{if(controller.signal.aborted)return;
   const saved=view.intent??initial;setIntent(saved);setIpv4(saved.ipv4.join(', '));setIpv6(saved.ipv6.join(', '));
   setRedirects(saved.redirect_urls.join('\n'));setRevision(view.revision);setPreview(view.preview);setLoaded(true);
   setStatus(view.state==='prepared'?'Preparation saved. Runtime application and public checks are still required.':'Enter the domain and application settings.');
  }).catch(()=>{if(!controller.signal.aborted)setError('Setup could not be loaded. Verify management access and refresh.');});
  return()=>{controller.abort();active.current?.abort();};
 },[client]);
 function update<K extends keyof DeploymentIntent>(field:K,value:DeploymentIntent[K]){
  setIntent(current=>({...current,[field]:value}));setPreview(null);setError('');setStatus('');
 }
 async function inspect(event:FormEvent){
  event.preventDefault();if(busy)return;setBusy(true);setError('');setStatus('Checking the configuration.');
  const controller=new AbortController();active.current=controller;
  try{const value=await client.preview(intent,controller.signal);if(!controller.signal.aborted){setPreview(value);setStatus('Review the records, callback and remaining checks.');}}
  catch(reason){if(!controller.signal.aborted){setError(reason instanceof Error?reason.message:'Configuration check failed.');setStatus('');form.current?.querySelector<HTMLInputElement>('input')?.focus();}}
  finally{if(!controller.signal.aborted)setBusy(false);}
 }
 async function save(){
  if(busy||!preview)return;setBusy(true);setError('');const controller=new AbortController();active.current=controller;
  try{const view=await client.save(intent,revision,controller.signal);if(!controller.signal.aborted){setRevision(view.revision);setPreview(view.preview);
   setStatus('Preparation saved. Apply the reviewed proxy and Auth settings, then run the required checks.');}}
  catch(reason){if(!controller.signal.aborted){setError(reason instanceof Error?reason.message:'Saving failed.');setStatus('');}}
  finally{if(!controller.signal.aborted)setBusy(false);}
 }
 const lines=(value:string)=>value.split(/[\s,]+/).filter(Boolean);
 return <>
  <div className="page-heading"><div><h1>Domain and email setup</h1><p className="muted">Prepare {environmentName} and the installation's public origin.</p></div></div>
  <p>The origin is shared by the installation. A change requires reviewing every environment's callbacks, email links and client URLs.</p>
  <p role="status" aria-live="polite">{status}</p>{error&&<p role="alert" className="error">{error}</p>}
  {!loaded?<p>Setup is unavailable until it loads.</p>:<form ref={form} onSubmit={inspect}>
   <fieldset className="details" disabled={busy}><legend>1. Domain and DNS</legend>
    <label>Setup profile<select value={intent.profile} onChange={event=>{
     const profile=event.target.value as DeploymentIntent['profile'];setIntent(current=>({...current,profile,tls:profile==='local'?'local':'managed',certificate_ref:null}));setPreview(null);
    }}><option value="public">Public deployment</option><option value="local">Local evaluation</option></select></label>
    <label>Public HTTPS origin<input name="public_url" type="url" autoComplete="url" required value={intent.public_url} onChange={event=>update('public_url',event.target.value)} aria-describedby="origin-help"/></label>
    <p id="origin-help" className="muted small">Use https://api.your-domain with no path. Local evaluation uses a loopback origin and an owned test CA.</p>
    <label>Server IPv4 addresses<input name="ipv4" autoComplete="off" value={ipv4} onChange={event=>{setIpv4(event.target.value);update('ipv4',lines(event.target.value));}}/></label>
    <label>Server IPv6 addresses<input name="ipv6" autoComplete="off" value={ipv6} onChange={event=>{setIpv6(event.target.value);update('ipv6',lines(event.target.value));}} aria-describedby="ipv6-help"/></label>
    <p id="ipv6-help" className="muted small">Leave IPv6 empty only when no AAAA record exists. A stale IPv6 record can break HTTPS for some clients.</p>
    <label className="check"><input name="provider_access" type="checkbox" checked={intent.provider_access} onChange={event=>update('provider_access',event.target.checked)}/>I can manage this domain's DNS records</label>
   </fieldset>
   <fieldset className="details" disabled={busy}><legend>2. HTTPS and renewal</legend>
    <label>Certificate management<select value={intent.tls} onChange={event=>{const tls=event.target.value as DeploymentIntent['tls'];setIntent(current=>({...current,tls,certificate_ref:tls==='external'?'':null}));setPreview(null);}}>
     {intent.profile==='local'?<option value="local">Owned local CA</option>:<><option value="managed">Managed certificate and renewal</option><option value="external">Operator-provided certificate</option></>}
    </select></label>
    {intent.tls==='managed'&&<label>Certificate account email<input name="acme_email" type="email" autoComplete="email" required value={intent.acme_email} onChange={event=>update('acme_email',event.target.value)}/></label>}
    {intent.tls==='external'&&<label>Private certificate reference<input name="certificate_ref" autoComplete="off" required value={intent.certificate_ref??''} onChange={event=>update('certificate_ref',event.target.value)}/></label>}
    <p className="muted small">Managed HTTPS requires external ports 80 and 443 and persistent private certificate storage. External certificates need a separate renewal and reload procedure.</p>
   </fieldset>
   <fieldset className="details" disabled={busy}><legend>3. Auth links and email</legend>
    <label>Application site URL<input name="site_url" type="url" autoComplete="url" required value={intent.site_url} onChange={event=>update('site_url',event.target.value)}/></label>
    <label>Allowed redirect URLs<textarea name="redirect_urls" value={redirects} onChange={event=>{setRedirects(event.target.value);update('redirect_urls',event.target.value.split('\n').map(line=>line.trim()).filter(Boolean));}}/></label>
    <label>Private SMTP reference<input name="smtp_ref" autoComplete="off" value={intent.smtp_ref??''} onChange={event=>update('smtp_ref',event.target.value||null)} aria-describedby="smtp-help"/></label>
    <p id="smtp-help" className="muted small">The operator creates this environment's private SMTP file. Enter its reference name only. The form never accepts a password or private key. Delivery tests need an explicitly owned recipient.</p>
   </fieldset>
   <button className="primary" type="submit" disabled={busy}>{busy?'Checking...':'Review setup'}</button>
  </form>}
  {preview&&<section className="details" aria-label="Setup review"><h2>Review before applying</h2>
   <h3>DNS records</h3><div style={{overflowX:'auto'}}><table><thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Value</th></tr></thead>
    <tbody>{preview.dns_records.map(record=><tr key={record.type+record.value}><td>{record.name}</td><td>{record.type}</td><td>{record.value}</td></tr>)}</tbody></table></div>
   <h3>OAuth callback</h3><p style={{overflowWrap:'anywhere'}}><code>{preview.callback_url}</code></p><p>Register this exact callback with each enabled OAuth provider.</p>
   {preview.warnings.map(warning=><p key={warning}>{warning}</p>)}
   <h3>Required verification</h3><ul>{preview.required_checks.map(check=><li key={check}>{check.replaceAll('_',' ')}: unproven</li>)}</ul>
   <p>Saving records preparation. Runtime application, mail delivery and public production acceptance require the corresponding observed checks.</p>
   <button className="primary" type="button" onClick={save} disabled={busy}>{busy?'Saving...':'Save preparation'}</button>
  </section>}
 </>;
}

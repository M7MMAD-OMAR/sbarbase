import {useEffect,useState,type FormEvent} from 'react';
import {auth,managementPolicy,useData} from './api';
import {Brand} from './Brand';
import {ErrorMessage,Loading} from './components';
import {mfaError} from './mfa-state';

type Factor={id:string;friendly_name?:string;status:string;factor_type:string};
type Enrollment={id:string;totp:{qr_code:string;secret:string;uri:string}};

/** All codes and enrollment secrets go directly through original Supabase Auth MFA. */
function Authenticator({factors,onVerified,onCancel,enrollmentOnly=false}:{factors:Factor[];onVerified:()=>void;onCancel?:()=>void;enrollmentOnly?:boolean}){
 const verified=factors.filter(factor=>factor.factor_type==='totp'&&factor.status==='verified');
 const [selected,setSelected]=useState(verified[0]?.id??''),[enrollment,setEnrollment]=useState<Enrollment>(),[code,setCode]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const enrolling=enrollmentOnly||!verified.length;
 async function enroll(){
  if(busy)return;setBusy(true);setError('');
  try {
   // Auth never returns a previously created secret, so remove an abandoned factor first.
   for(const factor of factors.filter(factor=>factor.status==='unverified'&&factor.factor_type==='totp')){
    const result=await auth.auth.mfa.unenroll({factorId:factor.id});if(result.error){setError(mfaError(result.error));return;}
   }
   const {data,error}=await auth.auth.mfa.enroll({factorType:'totp',friendlyName:'Authenticator '+new Date().toISOString().slice(0,19),issuer:'Sbarbase'});
   if(error)setError(mfaError(error));else if(data){setEnrollment(data);setSelected(data.id);}
  }catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);}
 }
 async function submit(event:FormEvent){event.preventDefault();if(busy)return;setError('');
  if(!/^\d{6}$/.test(code)){setError('Enter the six digit code from your authenticator.');return;}
  setBusy(true);
  try{const {error}=await auth.auth.mfa.challengeAndVerify({factorId:selected,code});if(error)setError(mfaError(error));else{setCode('');setEnrollment(undefined);onVerified();}}
  catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);}
 }
 async function cancel(){
  if(enrollment){setBusy(true);const {error}=await auth.auth.mfa.unenroll({factorId:enrollment.id});setBusy(false);if(error){setError(mfaError(error));return;}}
  setEnrollment(undefined);setCode('');onCancel?.();
 }
 return <section aria-labelledby="mfa-title">
  <h1 id="mfa-title">{enrolling?'Set up an authenticator':'Verify your sign in'}</h1>
  <p className="muted">{enrolling?'Use an authenticator app to protect your management account.':'Enter the current code from your authenticator to open the console.'}</p>
  {enrolling&&!enrollment?<button className="primary" disabled={busy} onClick={()=>void enroll()}>{busy?'Preparing...':'Set up authenticator'}</button>:<>
   {enrollment&&<><p>Scan this QR code in your authenticator app.</p><img src={enrollment.totp.qr_code} alt="Authenticator setup QR code" width="240" height="240"/><details><summary>Enter the setup key manually</summary><p><code>{enrollment.totp.secret}</code></p></details><p className="small muted">Keep this key private. It gives access to your account.</p></>}
   <form onSubmit={submit} aria-busy={busy} noValidate>
    {!enrolling&&verified.length>1&&<><label htmlFor="mfa-factor">Authenticator</label><select id="mfa-factor" value={selected} disabled={busy} onChange={event=>{setSelected(event.target.value);setCode('');setError('');}}>{verified.map(factor=><option key={factor.id} value={factor.id}>{factor.friendly_name??'Authenticator'}</option>)}</select></>}
    <label htmlFor="mfa-code">Six digit code</label><input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" autoFocus pattern="[0-9]{6}" maxLength={6} required value={code} disabled={busy} onChange={event=>{setCode(event.target.value.replace(/\D/g,''));setError('');}} aria-invalid={error?true:undefined} aria-describedby={error?'mfa-error':undefined}/>
    <div id="mfa-error"><ErrorMessage message={error}/></div><button className="primary auth-submit" disabled={busy||!selected}>{busy?'Verifying...':'Verify code'}</button>
   </form>
  </>}
  {!enrollment&&<ErrorMessage message={error}/>}
  {onCancel&&<button disabled={busy} onClick={()=>void cancel()}>Cancel</button>}
 </section>;
}

export function MfaGate({onVerified}:{onVerified:()=>void}){
 const [factors,setFactors]=useState<Factor[]>(),[error,setError]=useState(''),[version,setVersion]=useState(0);
 useEffect(()=>{let active=true;
  auth.auth.mfa.listFactors().then(({data,error})=>{if(active){if(error)setError(mfaError(error));else setFactors(data.all);}}).catch(()=>{if(active)setError('Unable to reach the server. Try again.');});
  return()=>{active=false;};
 },[version]);
 return <main className="auth-layout mfa-layout"><section className="auth-main"><Brand/><div className="login-panel">
  {factors?<Authenticator factors={factors} onVerified={onVerified}/>:error?<><ErrorMessage message={error}/><button onClick={()=>{setError('');setVersion(value=>value+1);}}>Try again</button></>:<Loading/>}
  <p className="account-note">Lost access to every authenticator? Contact your installation owner for account recovery.</p>
  <button onClick={()=>void auth.auth.signOut({scope:'local'})}>Sign out</button>
 </div></section></main>;
}

export function MfaSettings(){
 const policy=useData(signal=>managementPolicy(undefined,signal),[]);
 const [policyTarget,setPolicyTarget]=useState<boolean>(),[policyError,setPolicyError]=useState('');
 const [factors,setFactors]=useState<Factor[]>(),[adding,setAdding]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function load(){const {data,error}=await auth.auth.mfa.listFactors();if(error)setError(mfaError(error));else setFactors(data.all);}
 useEffect(()=>{void load().catch(()=>setError('Unable to reach the server. Try again.'));},[]);
 async function remove(factor:Factor){
  if(busy)return;setBusy(true);setError('');
  try {const {error}=await auth.auth.mfa.unenroll({factorId:factor.id});if(error)setError('The authenticator could not be removed. Verify your session and check the installation MFA policy.');
   else{await auth.auth.refreshSession();await load();}}
  catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);}
 }
 if(!factors)return error?<ErrorMessage message={error}/>:<Loading/>;
 if(policyTarget!==undefined)return <><p>Verify a current code to {policyTarget?'require MFA for this installation':'allow password sign-in for accounts without MFA'}. This ends all console and dashboard sessions.</p><ErrorMessage message={policyError}/><Authenticator factors={factors} onCancel={()=>setPolicyTarget(undefined)} onVerified={()=>{void managementPolicy(policyTarget).then(()=>auth.auth.signOut({scope:'local'})).catch(error=>setPolicyError((error as Error).message));}}/></>;
 if(adding)return <Authenticator enrollmentOnly factors={factors} onVerified={()=>{setAdding(false);void load();}} onCancel={()=>{setAdding(false);void load();}}/>;
 const verified=factors.filter(factor=>factor.factor_type==='totp'&&factor.status==='verified');
 return <section><h1>Account security</h1><p>Protect your account with an authenticator. You can add a spare and keep it in a separate safe place.</p>
  <ErrorMessage message={error}/><ul>{verified.map(factor=><li key={factor.id}>{factor.friendly_name??'Authenticator'} <button disabled={busy||(policy.data?.mfa_required!==false&&verified.length<2)} onClick={()=>void remove(factor)}>Remove</button></li>)}</ul>
  {!verified.length&&<p className="muted">{policy.data?.mfa_required===false?'MFA is optional. You can sign in with your password or add an authenticator.':'This installation requires an authenticator.'}</p>}
  <ErrorMessage message={policy.error}/>
  {policy.data?.can_manage_policy&&<section className="details"><h2>Installation sign-in policy</h2><p>{policy.data.mfa_required?'MFA is required for every management account.':'MFA is optional. Accounts with an enabled authenticator still verify it at sign-in.'}</p><button disabled={busy} onClick={()=>{setPolicyError('');setPolicyTarget(!policy.data!.mfa_required);}}>{policy.data.mfa_required?'Make MFA optional':'Require MFA'}</button><p className="small muted">A fresh authenticator code is required to change this setting. All active console and Supabase dashboard sessions will end.</p></section>}
  <button className="primary" disabled={busy} onClick={()=>setAdding(true)}>Add authenticator</button>
 </section>;
}

import {useEffect,useRef,useState,type FormEvent} from 'react';
import type {Session} from '@supabase/supabase-js';
import {ArrowRight} from 'lucide-react';
import {auth} from './api';
import {Brand} from './Brand';
import {ErrorMessage,ThemeControl} from './components';
import {newPasswordError} from './validation';
import {Attribution} from './Attribution';

/** The invitation token from a link like `/#invite=<token>`, or ''. */
export function inviteToken():string {
 const match=location.hash.match(/^#invite=([A-Za-z0-9_-]{43})$/);return match?match[1]!:'';
}
const clear=()=>history.replaceState(null,'',location.pathname+location.search);
async function redeem(body:Record<string,string>,session?:string) {
 const response=await fetch('/management/invitations/redeem',{method:'POST',headers:{'content-type':'application/json',
  ...(session?{authorization:'Bearer '+session}:{})},body:JSON.stringify(body)});
 const value=await response.json().catch(()=>({})) as {message?:string;email?:string;data?:{email:string}};
 return {status:response.status,message:value.message??'The request failed. Try again.',email:value.data?.email??value.email};
}

/** Accept an invitation without an account: choose a password, then sign in with it. */
export function AcceptInvitation({token,onSignIn}:{token:string;onSignIn:()=>void}){
 const [password,setPassword]=useState(''),[again,setAgain]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [passwordIssue,setPasswordIssue]=useState(''),[againIssue,setAgainIssue]=useState('');
 const passwordInput=useRef<HTMLInputElement>(null),againInput=useRef<HTMLInputElement>(null);
 const confirmationError=(value:string,first=password)=>!value?'Enter the password again.':value!==first?'The two passwords differ.':'';
 async function submit(event:FormEvent){event.preventDefault();if(busy)return;setError('');
  const first=newPasswordError(password),second=confirmationError(again);setPasswordIssue(first);setAgainIssue(second);
  if(first||second){(first?passwordInput:againInput).current?.focus();return;}
  setBusy(true);
  try{const result=await redeem({token,password});
   if(result.status===201&&result.email){clear();const {error}=await auth.auth.signInWithPassword({email:result.email,password});if(error)setError('Your account is ready. Sign in with your email and new password.');return;}
   if(result.status===409){setError(result.message);return;}
   setError(result.message);}
  catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);}}
 return <main className="auth-layout"><section className="auth-main" aria-labelledby="invite-title"><Brand/><div className="login-panel">
  <div className="auth-intro"><h1 id="invite-title">You are invited</h1><p className="muted">Choose a password to create your account and join the organization.</p></div>
  <form onSubmit={submit} noValidate aria-busy={busy}>
   <label htmlFor="new-password">Password (12 characters or more)</label><input ref={passwordInput} id="new-password" type="password" autoComplete="new-password" minLength={12} required disabled={busy} value={password} onChange={e=>{const value=e.target.value;setPassword(value);if(passwordIssue)setPasswordIssue(newPasswordError(value));if(againIssue)setAgainIssue(confirmationError(again,value));}} onBlur={()=>setPasswordIssue(newPasswordError(password))} aria-invalid={passwordIssue?true:undefined} aria-describedby={passwordIssue?'new-password-error':undefined}/>
   {passwordIssue&&<p className="field-error" id="new-password-error" role="alert">{passwordIssue}</p>}
   <label htmlFor="again">Password again</label><input ref={againInput} id="again" type="password" autoComplete="new-password" required disabled={busy} value={again} onChange={e=>{const value=e.target.value;setAgain(value);if(againIssue)setAgainIssue(confirmationError(value));}} onBlur={()=>setAgainIssue(confirmationError(again))} aria-invalid={againIssue?true:undefined} aria-describedby={againIssue?'again-error':undefined}/>
   {againIssue&&<p className="field-error" id="again-error" role="alert">{againIssue}</p>}
   <ErrorMessage message={error}/>
   <button className="primary auth-submit" disabled={busy}>{busy?'Joining…':'Create account and join'}{!busy&&<ArrowRight aria-hidden="true"/>}</button>
  </form>
  <p className="account-note">Already have an account? <button className="project-link" onClick={onSignIn}>Sign in first</button>, then open the invitation link again.</p>
 </div><div className="auth-bottom"><div className="auth-footer"><ThemeControl/></div><Attribution/></div></section></main>;
}

/** Signed in with an invitation link open: show what it offers and join only when the person
 * confirms, so a link someone sends cannot add them to an organization silently. */
export function JoinInvitation({session,onJoined}:{session:Session;onJoined:()=>void}){
 const [offer,setOffer]=useState<{organization:string;role:string;email:string}>(),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[joinError,setJoinError]=useState('');
 const token=inviteToken();
 useEffect(()=>{if(!token)return;
  void fetch('/management/invitations/redeem',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token,preview:true})})
   .then(async response=>{const value=await response.json().catch(()=>({})) as {data?:{organization:string;role:string;email:string};message?:string};
    if(response.ok&&value.data)setOffer(value.data);else{clear();setMessage(value.message??'This invitation is not valid.');}})
   .catch(()=>setMessage('Unable to reach the server. Try again.'));},[token]);
 async function join(){if(busy)return;setBusy(true);setJoinError('');
  try{const result=await redeem({token},session.access_token);
   if(result.status===200){clear();setOffer(undefined);onJoined();}
   else setJoinError(result.status===400?'This invitation is not valid for the account you are signed in with.':result.message);}
  catch{setJoinError('Unable to reach the server. Try again.');}finally{setBusy(false);}}
 if(message)return <p className="error" role="alert">{message}</p>;
 if(!offer)return null;
 return <section className="details" aria-labelledby="join-title" aria-busy={busy}><h2 id="join-title">Join {offer.organization}?</h2>
  <p className="muted small">This invitation, sent to {offer.email}, adds you to {offer.organization} as {offer.role}.</p>
  <ErrorMessage message={joinError}/><div className="form-row"><button className="primary" disabled={busy} onClick={()=>void join()}>{busy?'Joining…':`Join as ${offer.role}`}</button><button disabled={busy} onClick={()=>{clear();setOffer(undefined);}}>Not now</button></div></section>;
}

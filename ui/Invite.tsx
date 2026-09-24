import {useEffect,useState,type FormEvent} from 'react';
import type {Session} from '@supabase/supabase-js';
import {ArrowRight} from 'lucide-react';
import {auth} from './api';
import {Brand} from './Brand';
import {ErrorMessage,ThemeControl} from './components';

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
 async function submit(event:FormEvent){event.preventDefault();setError('');
  if(password!==again){setError('The two passwords differ.');return;}
  setBusy(true);
  try{const result=await redeem({token,password});
   if(result.status===201&&result.email){clear();const {error}=await auth.auth.signInWithPassword({email:result.email,password});if(error)setError('Your account is ready. Sign in with your email and new password.');return;}
   if(result.status===409){setError(result.message);return;}
   setError(result.message);}
  catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);}}
 return <main className="auth-layout"><section className="auth-main" aria-labelledby="invite-title"><Brand/><div className="login-panel">
  <div className="auth-intro"><h1 id="invite-title">You are invited</h1><p className="muted">Choose a password to create your account and join the organization.</p></div>
  <form onSubmit={submit} aria-busy={busy}>
   <label htmlFor="new-password">Password (12 characters or more)</label><input id="new-password" type="password" autoComplete="new-password" minLength={12} required disabled={busy} value={password} onChange={e=>setPassword(e.target.value)}/>
   <label htmlFor="again">Password again</label><input id="again" type="password" autoComplete="new-password" minLength={12} required disabled={busy} value={again} onChange={e=>setAgain(e.target.value)}/>
   <ErrorMessage message={error}/>
   <button className="primary auth-submit" disabled={busy}>{busy?'Joining…':'Create account and join'}{!busy&&<ArrowRight aria-hidden="true"/>}</button>
  </form>
  <p className="account-note">Already have an account? <button className="project-link" onClick={onSignIn}>Sign in first</button>, then open the invitation link again.</p>
 </div><div className="auth-footer"><ThemeControl/></div></section></main>;
}

/** Signed in with an invitation link open: join with the current session, once. */
export function useJoinWithSession(session:Session|null,onJoined:()=>void):string {
 const [message,setMessage]=useState('');
 useEffect(()=>{const token=inviteToken();if(!session||!token)return;
  void redeem({token},session.access_token).then(result=>{clear();
   if(result.status===200){setMessage('');onJoined();}
   else setMessage(result.status===400?'This invitation is not valid for the account you are signed in with.':result.message);});
 },[session?.access_token]);
 return message;
}

import {useRef,useState,type FormEvent} from 'react';
import {Eye,EyeOff,ArrowRight,LockKeyhole} from 'lucide-react';
import {auth} from './api';
import {ErrorMessage,ThemeControl} from './components';
import {Brand,CactusArtwork} from './Brand';
import {Attribution} from './Attribution';
import {emailError,loginPasswordError,signInError} from './validation';
export function Login(){
 const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[showPassword,setShowPassword]=useState(false);
 const [emailIssue,setEmailIssue]=useState(''),[passwordIssue,setPasswordIssue]=useState('');
 const emailInput=useRef<HTMLInputElement>(null),passwordInput=useRef<HTMLInputElement>(null);
 async function submit(event:FormEvent){event.preventDefault();if(busy)return;setError('');
  const first=emailError(email),second=loginPasswordError(password);setEmailIssue(first);setPasswordIssue(second);
  if(first||second){(first?emailInput:passwordInput).current?.focus();return;}
  setBusy(true);try{const {error}=await auth.auth.signInWithPassword({email:email.trim(),password});if(error)setError(signInError(error));}
  catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);}}
 return <main className="auth-layout">
  <section className="auth-main" aria-labelledby="login-title">
   <Brand/>
   <div className="login-panel">
    <div className="auth-intro"><h1 id="login-title">Welcome back</h1><p className="muted">Your projects. Your space to grow.</p></div>
    <form onSubmit={submit} noValidate aria-busy={busy}>
     <label htmlFor="email">Email address</label><input ref={emailInput} id="email" type="email" placeholder="you@example.com" autoComplete="username" required disabled={busy} value={email} onChange={e=>{const value=e.target.value;setEmail(value);setError('');if(emailIssue)setEmailIssue(emailError(value));}} onBlur={()=>setEmailIssue(emailError(email))} aria-invalid={emailIssue?true:undefined} aria-describedby={emailIssue?'email-error':error?'login-error':undefined}/>
     {emailIssue&&<p className="field-error" id="email-error" role="alert">{emailIssue}</p>}
     <label htmlFor="password">Password</label><div className="password-field"><input ref={passwordInput} id="password" type={showPassword?'text':'password'} placeholder="Enter your password" autoComplete="current-password" required disabled={busy} value={password} onChange={e=>{const value=e.target.value;setPassword(value);setError('');if(passwordIssue)setPasswordIssue(loginPasswordError(value));}} onBlur={()=>setPasswordIssue(loginPasswordError(password))} aria-invalid={passwordIssue?true:undefined} aria-describedby={passwordIssue?'password-error':error?'login-error':undefined}/><button type="button" className="password-toggle" disabled={busy} aria-label={showPassword?'Hide password':'Show password'} aria-pressed={showPassword} onClick={()=>setShowPassword(!showPassword)}>{showPassword?<EyeOff aria-hidden="true"/>:<Eye aria-hidden="true"/>}</button></div>
     {passwordIssue&&<p className="field-error" id="password-error" role="alert">{passwordIssue}</p>}
     <div id="login-error"><ErrorMessage message={error}/></div>
     <button className="primary auth-submit" disabled={busy}>{busy?'Signing in…':'Sign in'}{!busy&&<ArrowRight aria-hidden="true"/>}</button>
    </form>
    <p className="account-note">Need an account? <span>Contact your installation owner.</span></p>
   </div>
   <div className="auth-bottom"><div className="auth-footer"><LockKeyhole aria-hidden="true"/><span>Your own infrastructure. Your own data.</span><ThemeControl/></div><Attribution/></div>
  </section>
  <section className="auth-art-panel" aria-labelledby="art-title">
   <div className="art-topline"><span>A little space. A lot of possibility.</span><span className="art-edition">sbarbase / console</span></div>
   <CactusArtwork/>
   <div className="art-copy"><h2 id="art-title">Good things<br/>grow from here.</h2><p>A home for your projects, databases,<br className="desktop-break"/> and whatever you build next.</p></div>
   <div className="art-bottomline"><span className="brand-dot"/>Rooted in your infrastructure.</div>
  </section>
 </main>;
}

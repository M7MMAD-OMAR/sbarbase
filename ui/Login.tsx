import {useState,type FormEvent} from 'react';
import {Eye,EyeOff,ArrowRight,LockKeyhole} from 'lucide-react';
import {auth} from './api';
import {ErrorMessage} from './components';
import {Brand,CactusArtwork} from './Brand';
export function Login(){
 const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[showPassword,setShowPassword]=useState(false);
 async function submit(event:FormEvent){event.preventDefault();setBusy(true);setError('');try{const {error}=await auth.auth.signInWithPassword({email,password});if(error)setError('Unable to sign in. Check your email and password.');}catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);setPassword('');}}
 return <main className="auth-layout">
  <section className="auth-main" aria-labelledby="login-title">
   <Brand/>
   <div className="login-panel">
    <div className="auth-intro"><h1 id="login-title">Welcome back</h1><p className="muted">Your projects. Your space to grow.</p></div>
    <form onSubmit={submit} aria-busy={busy}>
     <label htmlFor="email">Email address</label><input id="email" type="email" placeholder="you@example.com" autoComplete="username" required disabled={busy} value={email} onChange={e=>setEmail(e.target.value)} aria-describedby={error?'login-error':undefined}/>
     <label htmlFor="password">Password</label><div className="password-field"><input id="password" type={showPassword?'text':'password'} placeholder="Enter your password" autoComplete="current-password" required disabled={busy} value={password} onChange={e=>setPassword(e.target.value)} aria-describedby={error?'login-error':undefined}/><button type="button" className="password-toggle" aria-label={showPassword?'Hide password':'Show password'} aria-pressed={showPassword} onClick={()=>setShowPassword(!showPassword)}>{showPassword?<EyeOff aria-hidden="true"/>:<Eye aria-hidden="true"/>}</button></div>
     <div id="login-error"><ErrorMessage message={error}/></div>
     <button className="primary auth-submit" disabled={busy}>{busy?'Signing in…':'Sign in'}{!busy&&<ArrowRight aria-hidden="true"/>}</button>
    </form>
    <p className="account-note">Need an account? <span>Contact your installation owner.</span></p>
   </div>
   <div className="auth-footer"><LockKeyhole aria-hidden="true"/><span>Your own infrastructure. Your own data.</span></div>
  </section>
  <section className="auth-art-panel" aria-labelledby="art-title">
   <div className="art-topline"><span>A little space. A lot of possibility.</span><span className="art-edition">sbarbase / console</span></div>
   <CactusArtwork/>
   <div className="art-copy"><h2 id="art-title">Good things<br/>grow from here.</h2><p>A home for your projects, databases,<br className="desktop-break"/> and whatever you build next.</p></div>
   <div className="art-bottomline"><span className="brand-dot"/>Rooted in your infrastructure.</div>
  </section>
 </main>;
}

import {useState,type FormEvent} from 'react';
import {auth} from './api';
import {ErrorMessage} from './components';
export function Login(){
 const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(event:FormEvent){event.preventDefault();setBusy(true);setError('');try{const {error}=await auth.auth.signInWithPassword({email,password});if(error)setError('Unable to sign in. Check your email and password.');}catch{setError('Unable to reach the server. Try again.');}finally{setBusy(false);setPassword('');}}
 return <main className="login"><div className="wordmark">sbarbase</div><section className="login-panel"><h1>Welcome back</h1><p className="muted">Sign in to manage your projects.</p><form onSubmit={submit} aria-busy={busy}><label htmlFor="email">Email</label><input id="email" type="email" autoComplete="username" required value={email} onChange={e=>setEmail(e.target.value)} aria-describedby={error?'login-error':undefined}/><label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)} aria-describedby={error?'login-error':undefined}/><div id="login-error"><ErrorMessage message={error}/></div><button className="primary" disabled={busy}>{busy?'Signing in…':'Sign in'}</button></form><p className="small muted">Accounts are created by your installation owner.</p></section></main>;
}

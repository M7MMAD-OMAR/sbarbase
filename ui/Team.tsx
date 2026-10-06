import {useRef,useState,type FormEvent} from 'react';
import {Users,UserPlus,Copy} from 'lucide-react';
import {useData,type Api,type Organization} from './api';
import {ErrorMessage,Loading,Refresh} from './components';
import {invitationEmailError} from './validation';
/** Who can act in this organization. Owners change a role or remove a member; access ends at once.
 * Adding someone new waits for invitations. */
function Members({organization,request}:{organization:Organization;request:Api}){
 const result=useData<{data:{actor:string;role:string}[]}>(signal=>request(`/organizations/${organization.id}/members`,'GET',undefined,signal),[organization.id,request]);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[confirm,setConfirm]=useState<string>();
 const owner=organization.role==='owner';
 async function change(actor:string,role:string|null){setBusy(true);setError('');
  try{await request(`/organizations/${organization.id}/members/${encodeURIComponent(actor)}`,role?'PUT':'DELETE',role?{role}:undefined);setConfirm(undefined);result.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="members" aria-labelledby="members-heading"><h2 id="members-heading"><Users aria-hidden="true"/>Members</h2><ErrorMessage message={error||result.error}/>{result.error&&<Refresh onClick={result.refresh}/>} {result.loading?<Loading/>:result.data&&<div className="table-wrap"><table><thead><tr><th>Operator</th><th>Role</th>{owner&&<th>Action</th>}</tr></thead><tbody>{result.data.data.map(member=><tr key={member.actor}><td><code title={member.actor}>{member.actor.slice(0,8)}</code></td>
 <td>{owner?<select aria-label={'Role of '+member.actor.slice(0,8)} value={member.role} disabled={busy} onChange={event=>void change(member.actor,event.target.value)}><option value="owner">owner</option><option value="admin">admin</option><option value="viewer">viewer</option></select>:member.role}</td>
 {owner&&<td>{confirm===member.actor?<div className="confirm"><span>Remove this member?</span><button className="danger" disabled={busy} onClick={()=>void change(member.actor,null)}>Confirm remove</button><button onClick={()=>setConfirm(undefined)}>Cancel</button></div>:<button disabled={busy} onClick={()=>setConfirm(member.actor)}>Remove</button>}</td>}</tr>)}</tbody></table></div>}</section>;
}
type Invitation={id:string;email:string;role:string;inviter:string;expires_at:number};
/** Invite someone by email. The link is shown once; the inviter sends it. */
function Invitations({organization,request}:{organization:Organization;request:Api}){
 const pending=useData<{data:Invitation[]}>(signal=>request(`/organizations/${organization.id}/invitations`,'GET',undefined,signal),[organization.id,request]);
 const [email,setEmail]=useState(''),[role,setRole]=useState('viewer'),[link,setLink]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState('');
 const emailInput=useRef<HTMLInputElement>(null),[emailIssue,setEmailIssue]=useState('');
 async function invite(event:FormEvent){event.preventDefault();if(busy)return;setError('');setCopied('');
  const issue=invitationEmailError(email);setEmailIssue(issue);if(issue){emailInput.current?.focus();return;}setBusy(true);
  try{const {data}=await request(`/organizations/${organization.id}/invitations`,'POST',{email:email.trim(),role}) as {data:{token:string}};
   setLink(`${location.origin}/#invite=${data.token}`);setEmail('');pending.refresh();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function cancel(id:string){setBusy(true);setError('');try{await request(`/organizations/${organization.id}/invitations/${id}`,'DELETE');pending.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function copy(){try{await navigator.clipboard.writeText(link);setCopied('Copied to clipboard.');}catch{setCopied('Copy unavailable. Select and copy the link manually.');}}
 return <section className="details"><h2>Invite someone</h2><p className="muted small">They get an account in this console and join {organization.name} with the role you choose. The link works once, for 7 days.</p>
 <form className="form-row" onSubmit={invite} noValidate aria-busy={busy}><label className="sr-only" htmlFor="invite-email">Email</label><input ref={emailInput} id="invite-email" type="email" placeholder="name@example.com" required disabled={busy} value={email} onChange={e=>{const value=e.target.value;setEmail(value);if(emailIssue)setEmailIssue(invitationEmailError(value));}} onBlur={()=>setEmailIssue(invitationEmailError(email))} aria-invalid={emailIssue?true:undefined} aria-describedby={emailIssue?'invite-email-error':undefined}/>
 <label className="sr-only" htmlFor="invite-role">Role</label><select id="invite-role" value={role} disabled={busy} onChange={e=>setRole(e.target.value)}>{organization.role==='owner'&&<option value="owner">owner</option>}<option value="admin">admin</option><option value="viewer">viewer</option></select>
 <button className="primary" disabled={busy}><UserPlus aria-hidden="true"/>{busy?'Creating invitation…':'Create invitation'}</button></form>{emailIssue&&<p className="field-error" id="invite-email-error" role="alert">{emailIssue}</p>}<ErrorMessage message={error||pending.error}/>{pending.error&&<Refresh onClick={pending.refresh}/>}
 {link&&<div className="new-key"><label htmlFor="invite-link">Send this link to the person you invited. It is only shown once.</label><textarea id="invite-link" readOnly value={link}/><div className="form-row"><button onClick={()=>void copy()}><Copy aria-hidden="true"/>Copy link</button><button onClick={()=>{setLink('');setCopied('');}}>Done</button></div><p className="small muted" role="status">{copied}</p></div>}
 {!!pending.data?.data.length&&<div className="table-wrap"><table><thead><tr><th>Pending</th><th>Role</th><th>Expires</th><th>Action</th></tr></thead><tbody>{pending.data.data.map(item=><tr key={item.id}><td>{item.email}</td><td>{item.role}</td><td>{new Date(item.expires_at).toLocaleDateString()}</td><td><button disabled={busy} onClick={()=>void cancel(item.id)}>Cancel</button></td></tr>)}</tbody></table></div>}</section>;
}
export function Team({organization,request}:{organization:Organization;request:Api}){
 return <><div className="page-heading"><div><h1>Team</h1><p className="muted">Manage who can access {organization.name} and invite collaborators.</p></div></div><Members organization={organization} request={request}/><Invitations organization={organization} request={request}/></>;
}

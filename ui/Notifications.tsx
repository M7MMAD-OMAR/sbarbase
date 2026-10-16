import {useCallback,useEffect,useState,type FormEvent} from 'react';
import {Bell,CircleCheck,ExternalLink,Send,Unplug} from 'lucide-react';
import {ErrorMessage,Loading} from './components';

export type TelegramStatus={state:'off'|'pending'|'linked';bot?:string;tokenHint?:string;chat?:string;link?:string;expiresAt?:number};
type View={data:TelegramStatus;owner:boolean};

/** The Telegram routes keep their own messages: a refusal from the server says what to do next
 * (a wrong token, no Start yet), which the console's generic messages would hide. */
export function telegramClient(token:()=>string){
 return async(path:string,method:string,body?:unknown):Promise<View>=>{
  let response:Response;
  try{response=await fetch('/management/v1/notifications/telegram'+path,{method,headers:{authorization:'Bearer '+token(),
   ...(body===undefined?{}:{'content-type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(20000)});}
  catch{throw new Error('Unable to reach the server. Check your connection and try again.');}
  const value=await response.json().catch(()=>undefined) as {data?:TelegramStatus;owner?:boolean;message?:string}|undefined;
  if(response.ok&&value?.data)return {data:value.data,owner:value.owner===true};
  if(response.status===401)throw new Error('Your session expired. Sign in again.');
  if(response.status===403)throw new Error('Only an owner of this installation can change notifications.');
  throw new Error(value?.message||'The request failed. Refresh and try again.');
 };
}
export type TelegramClient=ReturnType<typeof telegramClient>;

export function Notifications({client}:{client:TelegramClient}){
 const [view,setView]=useState<View>(),[error,setError]=useState(''),[busy,setBusy]=useState(''),[token,setToken]=useState(''),[notice,setNotice]=useState('');
 const run=useCallback(async(label:string,action:()=>Promise<View>,done?:string)=>{
  setBusy(label);setError('');setNotice('');
  try{setView(await action());if(done)setNotice(done);}catch(reason){setError(reason instanceof Error?reason.message:'The request failed.');}
  finally{setBusy('');}
 },[]);
 useEffect(()=>{void run('load',()=>client('','GET'));},[client,run]);
 const status=view?.data,owner=view?.owner===true;
 async function connect(event:FormEvent){event.preventDefault();await run('connect',()=>client('','PUT',{token:token.trim()}));setToken('');}
 return <><div className="page-heading"><div><h1>Notifications</h1><p className="muted">Where Sbarbase tells you about failed backups, updates and problems on this server.</p></div></div>
  <ErrorMessage message={error}/>{notice&&<p className="success" role="status">{notice}</p>}
  {!status?(busy==='load'?<Loading/>:null):<section className="details notification-channel">
   <h2><Bell aria-hidden="true"/>Telegram</h2>
   {status.state==='linked'&&<>
    <p><CircleCheck aria-hidden="true"/> Connected to <strong>@{status.bot}</strong>, chat {status.chat}. Notifications arrive within a minute.</p>
    {owner&&<div className="actions">
     <button type="button" className="primary" disabled={!!busy} onClick={()=>void run('test',()=>client('/test','POST'),'Test message sent. Check Telegram.')}><Send aria-hidden="true"/>Send a test message</button>
     <button type="button" className="secondary" disabled={!!busy} onClick={()=>{if(window.confirm('Disconnect Telegram? Sbarbase forgets the bot token and stops sending to this chat.'))void run('off',()=>client('','DELETE'),'Telegram is disconnected.');}}><Unplug aria-hidden="true"/>Disconnect</button>
    </div>}
   </>}
   {status.state==='pending'&&<>
    <p>Bot <strong>@{status.bot}</strong> is ready. Open it in Telegram and press <strong>Start</strong>, then come back here.</p>
    <div className="actions">
     <a className="button" href={status.link} target="_blank" rel="noopener noreferrer">Open @{status.bot} in Telegram <ExternalLink aria-hidden="true"/></a>
     {owner&&<button type="button" className="primary" disabled={!!busy} onClick={()=>void run('link',()=>client('/link','POST'),'Telegram is connected. A confirmation was sent to the chat.')}>I pressed Start</button>}
     {owner&&<button type="button" className="secondary" disabled={!!busy} onClick={()=>void run('off',()=>client('','DELETE'))}>Cancel</button>}
    </div>
    <p className="muted small">The link works once and expires after 30 minutes. Only the chat that opens it is connected.</p>
   </>}
   {status.state==='off'&&(owner?<form onSubmit={connect}>
    <ol className="small">
     <li>In Telegram, open <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">@BotFather</a> and send <code>/newbot</code>.</li>
     <li>Choose a name; BotFather replies with a token like <code>123456789:AA…</code>.</li>
     <li>Paste it here. It is checked with Telegram and kept only on this server.</li>
    </ol>
    <label htmlFor="telegram-token">Bot token</label>
    <input id="telegram-token" type="password" autoComplete="off" spellCheck={false} value={token} onChange={event=>setToken(event.target.value)} placeholder="123456789:AA…" required/>
    <div className="actions"><button type="submit" className="primary" disabled={!!busy||!token.trim()}>{busy==='connect'?'Checking…':'Connect'}</button></div>
    <p className="muted small">Never share the token anywhere else. If it was exposed, send <code>/revoke</code> to @BotFather and paste the new one.</p>
   </form>:<p className="muted">Telegram is not connected. An owner of this installation can connect it here.</p>)}
   {status.tokenHint&&<p className="muted small">Stored token ends in …{status.tokenHint}. It is never shown in full.</p>}
  </section>}
 </>;
}

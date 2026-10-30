import {useEffect,useId,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {Check,ChevronDown,Copy} from 'lucide-react';

/** Small controls of the Functions pages. None uses a browser default control or an inline style. */

/** One of several choices in a popup list: a select that follows the console's look, with arrow keys, Enter, Escape and a
 * click outside. The popup sits under the button with logical offsets, so it mirrors right to left. */
export function Choice<T extends string>({label,value,options,onChange,icon,wide}:{label:string;value:T;options:{id:T;label:string;hint?:string}[];
 onChange:(value:T)=>void;icon?:ReactNode;wide?:boolean}){
 const [open,setOpen]=useState(false),[active,setActive]=useState(0),root=useRef<HTMLDivElement>(null),button=useRef<HTMLButtonElement>(null),id=useId();
 const selected=options.find(option=>option.id===value)??options[0]!;
 useEffect(()=>{
  if(!open)return;
  const away=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false);};
  document.addEventListener('mousedown',away);return()=>document.removeEventListener('mousedown',away);
 },[open]);
 function choose(index:number){onChange(options[index]!.id);setOpen(false);button.current?.focus();}
 function keys(event:KeyboardEvent){
  if(!open&&(event.key==='ArrowDown'||event.key==='ArrowUp')){event.preventDefault();setActive(Math.max(0,options.findIndex(option=>option.id===value)));setOpen(true);return;}
  if(!open)return;
  if(event.key==='Escape'){event.preventDefault();setOpen(false);button.current?.focus();}
  else if(event.key==='ArrowDown'){event.preventDefault();setActive(index=>(index+1)%options.length);}
  else if(event.key==='ArrowUp'){event.preventDefault();setActive(index=>(index-1+options.length)%options.length);}
  else if(event.key==='Home'){event.preventDefault();setActive(0);}
  else if(event.key==='End'){event.preventDefault();setActive(options.length-1);}
  else if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(active);}
  else if(event.key==='Tab')setOpen(false);
 }
 return <div className={'fw-choice'+(wide?' wide':'')} ref={root} onKeyDown={keys}>
  <button ref={button} type="button" className="fw-ghost" role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-label={label} aria-controls={open?id:undefined} aria-activedescendant={open?`${id}-${active}`:undefined}
   onClick={()=>{setActive(Math.max(0,options.findIndex(option=>option.id===value)));setOpen(!open);}}>{icon}<span className="fw-choice-text">{selected.label}</span><ChevronDown aria-hidden="true"/></button>
  {open&&<ul className="fw-popup" role="listbox" id={id} aria-label={label}>
   {options.map((option,index)=><li key={option.id} id={`${id}-${index}`} role="option" aria-selected={option.id===value} className={(index===active?'active ':'')+(option.id===value?'chosen':'')}
    onMouseEnter={()=>setActive(index)} onMouseDown={event=>{event.preventDefault();choose(index);}}>
    <span>{option.label}{option.hint&&<small>{option.hint}</small>}</span>{option.id===value&&<Check aria-hidden="true"/>}</li>)}
  </ul>}
 </div>;
}

/** Tabs with the roles and arrow keys of the WAI tabs pattern. The caller renders the panel with the same id. */
export function Tabs<T extends string>({label,tabs,value,onChange,prefix}:{label:string;tabs:{id:T;label:string}[];value:T;onChange:(value:T)=>void;prefix:string}){
 const refs=useRef<Record<string,HTMLButtonElement|null>>({});
 function move(event:KeyboardEvent,index:number){
  const step=event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0;
  const rtl=getComputedStyle(event.currentTarget).direction==='rtl';
  let next=index;
  if(step)next=(index+(rtl?-step:step)+tabs.length)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;
  event.preventDefault();onChange(tabs[next]!.id);refs.current[tabs[next]!.id]?.focus();
 }
 return <div className="fw-tabs" role="tablist" aria-label={label}>{tabs.map((tab,index)=><button key={tab.id} ref={node=>{refs.current[tab.id]=node;}} role="tab" type="button"
  id={`${prefix}-tab-${tab.id}`} aria-selected={tab.id===value} aria-controls={`${prefix}-panel-${tab.id}`} tabIndex={tab.id===value?0:-1}
  className={tab.id===value?'active':''} onClick={()=>onChange(tab.id)} onKeyDown={event=>move(event,index)}>{tab.label}</button>)}</div>;
}

/** A row of mutually exclusive buttons, such as the time range. */
export function Segmented<T extends string>({label,options,value,onChange}:{label:string;options:{id:T;label:string}[];value:T;onChange:(value:T)=>void}){
 return <div className="fw-segmented" role="radiogroup" aria-label={label}>{options.map(option=><button key={option.id} type="button" role="radio" aria-checked={option.id===value}
  className={option.id===value?'active':''} onClick={()=>onChange(option.id)}>{option.label}</button>)}</div>;
}

export function CopyButton({text,label,quiet}:{text:string;label:string;quiet?:boolean}){
 const [done,setDone]=useState(false);
 return <button type="button" className={quiet?'fw-icon':'fw-ghost'} aria-label={done?'Copied':label} title={done?'Copied':label} onClick={()=>{
  void navigator.clipboard.writeText(text).then(()=>{setDone(true);setTimeout(()=>setDone(false),1500);},()=>{});}}>
  {done?<Check aria-hidden="true"/>:<Copy aria-hidden="true"/>}{!quiet&&<span>{done?'Copied':'Copy'}</span>}</button>;
}

export function Chip({tone,children}:{tone?:'ok'|'bad'|'wait'|'muted';children:ReactNode}){return <span className={'fw-chip '+(tone??'muted')}>{children}</span>;}

/** A delete that asks for the function's name, so it cannot be done by a stray click. */
export function ConfirmByName({name,busy,onConfirm,onCancel,action}:{name:string;busy:boolean;onConfirm:()=>void;onCancel:()=>void;action:string}){
 const [typed,setTyped]=useState(''),id=useId();
 return <div className="fw-confirm"><label htmlFor={id}>Type <code>{name}</code> to confirm</label>
  <input id={id} autoFocus autoComplete="off" spellCheck={false} value={typed} onChange={event=>setTyped(event.target.value)}/>
  <div className="fw-actions"><button type="button" className="fw-danger-solid" disabled={busy||typed!==name} onClick={onConfirm}>{action}</button>
   <button type="button" className="fw-ghost" disabled={busy} onClick={onCancel}>Cancel</button></div></div>;
}

export function Notice({tone,children}:{tone:'bad'|'wait'|'info';children:ReactNode}){return <p className={'fw-notice '+tone} role={tone==='bad'?'alert':'status'}>{children}</p>;}

/** What the pages show for a failed request, in the workspace's own look, with a way to try again. */
export function Problem({message,retry}:{message:string;retry?:()=>void}){
 return message?<div className="fw-notice bad" role="alert"><span>{message}</span>{retry&&<button type="button" className="fw-ghost small" onClick={retry}>Try again</button>}</div>:null;
}
export function Nothing({children}:{children:ReactNode}){return <p className="fw-empty-block">{children}</p>;}

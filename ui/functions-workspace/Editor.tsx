import {memo,useLayoutEffect,useMemo,useRef,type ChangeEvent,type KeyboardEvent} from 'react';
import type {Token} from './highlight';
import {languageOf,lines} from './highlight';
import {enter,lineCount,tab} from './editing';

const HIGHLIGHT_LIMIT=150_000;

/** One line of the highlighted copy. It is redrawn only when its own text or colours change, so typing in a long file redraws one line. */
const Row=memo(function Row({tokens}:{tokens:Token[];sig:string}){
 return <span className="line">{tokens.map((token,position)=><span key={position} className={token.kind==='plain'?undefined:'tok-'+token.kind}>{token.text}</span>)}{'\n'}</span>;
},(before,after)=>before.sig===after.sig);

/** A code editor made of what the console's content security policy allows: a textarea with transparent text over a highlighted copy
 * of the same text, and a column of line numbers. The textarea is as large as the text, so one scroll container moves all three and
 * nothing has to be kept in step. No library, no inline style. Tab indents (Escape, then Tab, leaves the field), Enter keeps the
 * indentation. */
export function Editor({value,path,label,onChange,readOnly}:{value:string;path:string;label:string;onChange:(value:string)=>void;readOnly?:boolean}){
 const input=useRef<HTMLTextAreaElement>(null),box=useRef<HTMLDivElement>(null),probe=useRef<HTMLSpanElement>(null),leaving=useRef(false);
 const language=value.length>HIGHLIGHT_LIMIT?'plain':languageOf(path);
 const rows=useMemo(()=>lines(value,language),[value,language]);
 const count=lineCount(value);
 const caret=useRef<{start:number;end:number}|undefined>(undefined);
 // A change made by a key is put in the text, then the selection is set once React has drawn it.
 useLayoutEffect(()=>{if(caret.current&&input.current){input.current.setSelectionRange(caret.current.start,caret.current.end);caret.current=undefined;}});
 /** Puts an edit in the text the way typing would, so the browser keeps its undo history and scrolls the caret into view: the part
  * that differs is selected and replaced with `insertText`. If the browser will not, the whole text is set through React. */
 function apply(area:HTMLTextAreaElement,edit:{value:string;start:number;end:number}){
  const old=area.value;
  // An edit that changes nothing (Shift+Tab on a line with no indent) must not touch the text, or the undo history.
  if(edit.value===old){area.setSelectionRange(edit.start,edit.end);return;}
  let from=0;
  while(from<old.length&&from<edit.value.length&&old[from]===edit.value[from])from++;
  let tail=0;
  while(tail<old.length-from&&tail<edit.value.length-from&&old[old.length-1-tail]===edit.value[edit.value.length-1-tail])tail++;
  const inserted=edit.value.slice(from,edit.value.length-tail);
  area.setSelectionRange(from,old.length-tail);
  const done=inserted?document.execCommand('insertText',false,inserted):document.execCommand('delete');
  if(done&&area.value===edit.value){area.setSelectionRange(edit.start,edit.end);reveal();return;}
  caret.current=edit;onChange(edit.value);
 }
 /** Scrolls the box so the caret is on screen: the textarea is as large as the text, so the browser does not do it for a caret far to
  * the right or below the visible part. The font is monospace, so the caret's place is its column and line times one character and one row. */
 function reveal(){
  const area=input.current,outer=box.current,mark=probe.current;if(!area||!outer||!mark)return;
  const before=area.value.slice(0,area.selectionEnd),row=before.split('\n').length-1,col=before.length-before.lastIndexOf('\n')-1;
  const width=mark.getBoundingClientRect().width/10,gutter=(outer.firstElementChild as HTMLElement).offsetWidth;
  const x=16+col*width,y=12+row*20;
  if(x<outer.scrollLeft)outer.scrollLeft=Math.max(0,x-24);else if(x+width>outer.scrollLeft+outer.clientWidth-gutter)outer.scrollLeft=x+width-outer.clientWidth+gutter+24;
  if(y<outer.scrollTop)outer.scrollTop=y;else if(y+20>outer.scrollTop+outer.clientHeight)outer.scrollTop=y+20-outer.clientHeight+12;
 }
 function keys(event:KeyboardEvent<HTMLTextAreaElement>){
  if(readOnly)return;
  // A key that confirms an input method composition is not an editing key.
  if(event.nativeEvent.isComposing||event.keyCode===229)return;
  const area=event.currentTarget;
  if(event.key==='Escape'){leaving.current=true;return;}
  if(event.key==='Tab'&&!event.ctrlKey&&!event.altKey&&!event.metaKey){
   if(leaving.current){leaving.current=false;return;}
   event.preventDefault();
   apply(area,tab(area.value,area.selectionStart,area.selectionEnd,event.shiftKey));return;
  }
  leaving.current=false;
  if(event.key==='Enter'&&!event.shiftKey&&!event.ctrlKey&&!event.metaKey&&!event.altKey){
   event.preventDefault();
   apply(area,enter(area.value,area.selectionStart,area.selectionEnd));
  }
 }
 return <div className="fw-ed" ref={box}>
  <div className="fw-ed-gutter" aria-hidden="true">{Array.from({length:count},(_,index)=><span key={index}>{index+1}</span>)}</div>
  <div className="fw-ed-main">
   <pre className="fw-ed-layer" aria-hidden="true"><span className="fw-ed-probe" ref={probe}>0000000000</span>{rows.map((tokens,index)=><Row key={index} tokens={tokens} sig={tokens.map(token=>token.kind[0]+token.text).join('\u0001')}/>)}</pre>
   <textarea ref={input} className="fw-ed-input" aria-label={label} spellCheck={false} autoCapitalize="off" autoCorrect="off" wrap="off" readOnly={readOnly}
    value={value} onChange={(event:ChangeEvent<HTMLTextAreaElement>)=>{onChange(event.target.value);requestAnimationFrame(reveal);}} onKeyDown={keys} onKeyUp={event=>{if(/^(Arrow|Home|End|Page)/.test(event.key))reveal();}} onClick={()=>reveal()} onBlur={()=>{leaving.current=false;}}/>
  </div></div>;
}

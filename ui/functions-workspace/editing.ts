/** Keyboard editing of the code editor's text, as pure functions of the text and the selection, so each rule is tested. */
export type Edit={value:string;start:number;end:number};
const INDENT='  ';

/** Tab inserts two spaces; with a selection it indents each line the selection touches; Shift+Tab takes up to two spaces off. */
export function tab(value:string,start:number,end:number,shift:boolean):Edit {
 const lineStart=value.lastIndexOf('\n',start-1)+1;
 // A selection of any size indents its lines; Tab never replaces selected text.
 const selection=end>start;
 if(!shift&&!selection)return {value:value.slice(0,start)+INDENT+value.slice(end),start:start+INDENT.length,end:start+INDENT.length};
 const stop=end>start&&value[end-1]==='\n'?end-1:end;
 const block=value.slice(lineStart,stop),lines=block.split('\n');
 let first=0,total=0;
 const changed=lines.map((line,index)=>{
  if(!shift){total+=INDENT.length;if(index===0)first=INDENT.length;return INDENT+line;}
  const remove=line.startsWith(INDENT)?2:line.startsWith(' ')?1:0;
  total-=remove;if(index===0)first=-remove;return line.slice(remove);
 });
 const next=value.slice(0,lineStart)+changed.join('\n')+value.slice(stop);
 return {value:next,start:Math.max(lineStart,start+first),end:end+total};
}

/** Enter keeps the indentation of the line, adds one level after an opening bracket, and puts a closing bracket on its own line. */
export function enter(value:string,start:number,end:number):Edit {
 const lineStart=value.lastIndexOf('\n',start-1)+1;
 const indent=/^[ \t]*/.exec(value.slice(lineStart,start))![0];
 const before=value[start-1],after=value[end];
 const opens=before==='{'||before==='['||before==='(';
 const closes=opens&&((before==='{'&&after==='}')||(before==='['&&after===']')||(before==='('&&after===')'));
 const inner=indent+(opens?INDENT:'');
 const insert='\n'+inner+(closes?'\n'+indent:'');
 const caret=start+1+inner.length;
 return {value:value.slice(0,start)+insert+value.slice(end),start:caret,end:caret};
}

export function lineCount(value:string):number {let count=1;for(let index=value.indexOf('\n');index!==-1;index=value.indexOf('\n',index+1))count++;return count;}

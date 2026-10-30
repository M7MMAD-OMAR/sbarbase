/** A small tokenizer for the code viewer: comments, strings, numbers and keywords of TypeScript and JavaScript, and the
 * same shapes in JSON. It only chooses a class for each piece of text, so the viewer needs no library and no inline
 * style (the console's content security policy refuses both a runtime style sheet and a style attribute). */
export type Kind='comment'|'string'|'number'|'keyword'|'plain';
export type Token={text:string;kind:Kind};
const KEYWORDS=new Set('as async await break case catch class const continue default delete do else enum export extends false finally for from function if implements import in instanceof interface let new null of private public readonly return static super switch this throw true try type typeof undefined var void while with yield'.split(' '));

export function languageOf(path:string):'code'|'plain' {return /\.(?:[cm]?[jt]sx?|json)$/.test(path)?'code':'plain';}

export function tokenize(source:string):Token[] {
 const tokens:Token[]=[];
 const push=(text:string,kind:Kind)=>{if(!text)return;const last=tokens[tokens.length-1];if(last&&last.kind===kind&&kind==='plain')last.text+=text;else tokens.push({text,kind});};
 let index=0;
 while(index<source.length){
  const rest=source.slice(index),char=source[index]!;
  let match:RegExpMatchArray|null;
  if((match=rest.match(/^\/\/[^\n]*/))||(match=rest.match(/^\/\*[\s\S]*?(?:\*\/|$)/))){push(match[0],'comment');index+=match[0].length;}
  else if(char==='"'||char==="'"||char==='`'){
   let end=index+1;
   while(end<source.length&&source[end]!==char&&(char==='`'||source[end]!=='\n')){end+=source[end]==='\\'?2:1;}
   end=Math.min(source.length,end+1);push(source.slice(index,end),'string');index=end;
  }
  else if((match=rest.match(/^(?:0x[0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?)n?\b/))){push(match[0],'number');index+=match[0].length;}
  else if((match=rest.match(/^[A-Za-z_$][\w$]*/))){push(match[0],KEYWORDS.has(match[0])?'keyword':'plain');index+=match[0].length;}
  else{push(char,'plain');index++;}
 }
 return tokens;
}

/** Tokens grouped by line, so the viewer can number them. */
export function lines(source:string,language:'code'|'plain'):Token[][] {
 const tokens=language==='code'?tokenize(source):[{text:source,kind:'plain' as Kind}];
 const result:Token[][]=[[]];
 for(const token of tokens){
  const parts=token.text.split('\n');
  parts.forEach((part,position)=>{if(position>0)result.push([]);if(part)result[result.length-1]!.push({text:part,kind:token.kind});});
 }
 return result;
}

/** Reads pasted `KEY=value` text into secrets, as a `.env` file is written: one pair a line, `export ` allowed, `#` comments, values in
 * single or double quotes (double quotes understand \n, \t, \" and \\), and a quoted value may run over several lines. */
export type Pair={name:string;value:string};

export function parseEnv(text:string):Pair[] {
 const pairs:Pair[]=[];
 const lines=text.replace(/\r\n?/g,'\n').split('\n');
 for(let index=0;index<lines.length;index++){
  const line=lines[index]!.trim();
  if(!line||line.startsWith('#'))continue;
  const match=/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s?(.*)$/.exec(line);
  if(!match)continue;
  const name=match[1]!;
  let rest=lines[index]!.slice(lines[index]!.indexOf('=')+1).replace(/^\s+/,'');
  const quote=rest[0];
  if(quote==='"'||quote==="'"){
   // Collect lines until the closing quote (not escaped, for double quotes).
   let body=rest.slice(1),closed=-1;
   for(;;){
    for(let at=0;at<body.length;at++){
     if(quote==='"'&&body[at]==='\\'){at++;continue;}
     if(body[at]===quote){closed=at;break;}
    }
    if(closed!==-1||index+1>=lines.length)break;
    body+='\n'+lines[++index]!;
   }
   const raw=closed===-1?body:body.slice(0,closed);
   pairs.push({name,value:quote==='"'?raw.replace(/\\(["\\nt])/g,(_,c:string)=>c==='n'?'\n':c==='t'?'\t':c):raw});
  } else {
   pairs.push({name,value:rest.replace(/\s+#.*$/,'').trimEnd()});
  }
 }
 // A name given twice keeps its last value, as a shell would.
 const last=new Map<string,string>();
 for(const pair of pairs)last.set(pair.name,pair.value);
 return [...last].map(([name,value])=>({name,value}));
}

/** True for pasted text that is more than one name or value: it has a line break or a name followed by `=`. */
export function looksLikeEnv(text:string):boolean {return /\n/.test(text.trim())||/^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=/.test(text);}

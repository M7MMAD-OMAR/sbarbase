import {test,expect} from 'bun:test';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';

const css=readFileSync(join(import.meta.dir,'..','ui','style.css'),'utf8');

/** The tokens of one palette block, by name. */
function palette(start:string):Record<string,string>{
 const from=css.indexOf(start);
 if(from<0)throw new Error('Missing palette block '+start);
 const body=css.slice(from+start.length,css.indexOf('}',from));
 return Object.fromEntries([...body.matchAll(/--([a-z0-9-]+):(#[0-9a-f]{6})\b/g)].map(match=>[match[1],match[2]]));
}
function luminance(hex:string){
 const channel=(index:number)=>{const value=parseInt(hex.slice(index,index+2),16)/255;return value<=0.04045?value/12.92:((value+0.055)/1.055)**2.4;};
 return 0.2126*channel(1)+0.7152*channel(3)+0.0722*channel(5);
}
function ratio(a:string,b:string){const [x,y]=[luminance(a),luminance(b)].sort((p,q)=>q-p);return (x+0.05)/(y+0.05);}

const light=palette(':root{color-scheme:light;');
const dark=palette(':root[data-theme=dark]{color-scheme:dark;');
const system=palette(':root:not([data-theme=light]){color-scheme:dark;');

/** WCAG 2.2 AA: 4.5:1 for text (1.4.3), 3:1 for control boundaries and focus indicators (1.4.11). */
const TEXT:[string,string][]=[];
for(const ink of ['text','muted','subtle'])for(const plate of ['page','rail','surface','surface-2','hover','selected','field','code-bg'])TEXT.push([ink,plate]);
TEXT.push(['accent','surface'],['accent','page'],['accent-fg','accent-solid'],['accent-fg','accent-solid-hover'],['danger-on','danger'],['danger-on','danger-hover'],
 ['danger-fg','surface'],['danger-fg','error-bg'],['text','error-bg'],['muted','error-bg'],['text','notice-bg'],['muted','notice-bg'],['notice-fg','surface'],['notice-fg','notice-bg'],
 ['text','ok-bg'],['ok-fg','ok-bg'],['ok-fg','surface']);
const BOUNDARY:[string,string][]=[['control-border','field'],['control-border','surface'],['ring','surface'],['ring','page'],['ring','field'],['accent-edge','surface']];

for(const [name,tokens] of [['light',light],['dark',dark]] as const){
 test(`the ${name} palette keeps text at WCAG AA contrast`,()=>{
  for(const [ink,plate] of TEXT){
   expect(tokens[ink],ink).toBeDefined();expect(tokens[plate],plate).toBeDefined();
   expect({pair:`${ink} on ${plate}`,ok:ratio(tokens[ink],tokens[plate])>=4.5}).toEqual({pair:`${ink} on ${plate}`,ok:true});
  }
 });
 test(`the ${name} palette keeps fields, focus rings and the primary edge visible`,()=>{
  for(const [edge,plate] of BOUNDARY)
   expect({pair:`${edge} against ${plate}`,ok:ratio(tokens[edge],tokens[plate])>=3}).toEqual({pair:`${edge} against ${plate}`,ok:true});
  // Cards must stand apart from the page they sit on.
  expect(ratio(tokens['border-strong'],tokens.page)).toBeGreaterThanOrEqual(1.6);
  // Read-only values are filled differently from editable fields.
  expect(tokens['code-bg']).not.toBe(tokens.field);
 });
}

test('the system dark palette is the explicit dark palette',()=>{
 expect(system).toEqual(dark);
});

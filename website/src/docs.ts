import {Marked,type Token,type Tokens} from 'marked';
import GithubSlugger from 'github-slugger';
import {readFileSync,readdirSync,statSync} from 'node:fs';
import {posix} from 'node:path';
import {content,docPage,repo,type Lang} from './content';
import {esc,icon,head,topbar,footer,pageData,homeOf} from './layout';

// The docs on the site, rendered at build time from ../docs with one pinned
// renderer; nothing is copied into website/. The rendered set is the docs map and
// docs/guides, docs/explain and docs/reference: the pages lab/test_docs_bilingual.py
// keeps in both languages. X.md is the English page, X.ar.md the Arabic one. Links
// into the set stay on the site, diagrams are served next to the pages, and every
// other link (engineering notes, evidence, source) goes to its GitHub page.

const ROOT=new URL('../../',import.meta.url);
const read=(path:string)=>readFileSync(new URL(path,ROOT),'utf8');
export const DIRECTORIES=['docs/guides','docs/explain','docs/reference'] as const;

export type Source={path:string,lang:Lang,slug:string};
export type Page={source:Source,url:string,other:string,title:string,html:string,ids:string[]};

const sourceOf=(path:string):Source=>{
 const lang:Lang=path.endsWith('.ar.md')?'ar':'en';
 const base=path.slice('docs/'.length).replace(/(\.ar)?\.md$/,'');
 return {path,lang,slug:base==='README'?'':base};
};

export function sources():Source[]{
 const paths=['docs/README.md','docs/README.ar.md'];
 for(const dir of DIRECTORIES)
  paths.push(...readdirSync(new URL(dir,ROOT)).filter(f=>f.endsWith('.md')).sort().map(f=>`${dir}/${f}`));
 return paths.map(sourceOf);
}

const isDirectory=(path:string)=>{try{return statSync(new URL(path,ROOT)).isDirectory();}catch{return false;}};

// Rewrites one Markdown href found in the page at from (a repository path).
export function rewrite(from:string,href:string,set:Map<string,Source>){
 if(/^[a-z][a-z0-9+.-]*:/i.test(href)||href.startsWith('//')||href.startsWith('#')||!href)return href;
 const cut=href.indexOf('#');
 const path=cut<0?href:href.slice(0,cut),fragment=cut<0?'':href.slice(cut);
 const resolved=posix.normalize(posix.join(posix.dirname(from),path));
 if(resolved.startsWith('..'))return href;
 const target=set.get(resolved);
 if(target)return docPage(target.lang,target.slug)+fragment;
 if(/^docs\/diagrams\/[^/]+\.svg$/.test(resolved))return `/${resolved}`;
 const tree=path.endsWith('/')||isDirectory(resolved);
 return `${repo}/${tree?'tree':'blob'}/main/${resolved.replace(/\/$/,'')}${tree&&path.endsWith('/')?'/':''}${fragment}`;
}

const unescape=(s:string)=>s.replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
const plain=(html:string)=>unescape(html.replace(/<[^>]+>/g,'')).replace(/\s+/g,' ').trim();
const SWITCH=/^\[(English|العربية)\]\([^)]*\)\s*\n/;

// The width and height of a diagram, from its viewBox, so the page does not jump
// while it loads.
function size(src:string){
 const m=src.startsWith('/docs/diagrams/')?read(src.slice(1)).match(/viewBox="[\d.]+ [\d.]+ ([\d.]+) ([\d.]+)"/):null;
 return m?` width="${Math.round(Number(m[1]))}" height="${Math.round(Number(m[2]))}"`:'';
}

function markdown(source:Source,text:string,set:Map<string,Source>){
 const c=content[source.lang];
 const slugger=new GithubSlugger();
 const ids:string[]=[];
 let title='',description='';
 const md=new Marked({gfm:true});
 md.use({
  walkTokens(token){
   if(token.type==='link'||token.type==='image')(token as Tokens.Link).href=rewrite(source.path,(token as Tokens.Link).href,set);
  },
  renderer:{
   // GitHub's anchors, so links such as server-deployment.md#private-directory-permissions land here too.
   heading({tokens,depth}){
    const text=plain(this.parser.parseInline(tokens,this.parser.textRenderer));
    const id=slugger.slug(text);ids.push(id);
    if(depth===1&&!title)title=text;
    return `<h${depth} id="${esc(id)}">${this.parser.parseInline(tokens)}</h${depth}>\n`;
   },
   paragraph({tokens}){
    const html=this.parser.parseInline(tokens);
    if(!description&&!html.startsWith('<img'))description=plain(html);
    return `<p>${html}</p>\n`;
   },
   image({href,text}){
    return `<img src="${esc(href)}" alt="${esc(text)}"${size(href)} loading="lazy" decoding="async">`;
   },
   code({text,lang}){
    const body=`<pre dir="ltr"><code${lang?` class="language-${esc(lang)}"`:''}>${esc(text)}</code></pre>`;
    if(lang==='mermaid')return `<details class="diagram-source"><summary>${esc(c.diagramSource)}</summary>${body}</details>\n`;
    return `<div class="code">${body}<button type="button" class="copy" data-copy="${esc(text)}" aria-label="${esc(c.copy)}">${icon('copy')}<span>${c.copy}</span></button></div>\n`;
   },
  },
 });
 const html=(md.parse(text.replace(SWITCH,'')) as string)
  .replaceAll('<table>','<div class="table-wrap"><table>').replaceAll('</table>','</table></div>')
  .replace(/<a href="(https?:\/\/[^"]+)"/g,'<a href="$1" rel="noopener noreferrer"');
 if(description.length>180)description=`${description.slice(0,description.lastIndexOf(' ',177))}…`;
 return {html,title,description,ids};
}

// The side list, read from the docs map of that language: one group per section
// that holds a list, each entry its first link. Entries outside the set point at GitHub.
type Group={title:string,items:{label:string,href:string}[]};
function navigation(lang:Lang,set:Map<string,Source>):Group[]{
 const path=lang==='ar'?'docs/README.ar.md':'docs/README.md';
 const tokens=new Marked({gfm:true}).lexer(read(path));
 const groups:Group[]=[];
 let current='';
 const firstLink=(list:Token[]):Tokens.Link|undefined=>{
  for(const t of list){
   if(t.type==='link')return t as Tokens.Link;
   const inner=(t as {tokens?:Token[]}).tokens;
   if(inner){const found=firstLink(inner);if(found)return found;}
  }
 };
 for(const t of tokens){
  if(t.type==='heading'&&(t as Tokens.Heading).depth===2)current=(t as Tokens.Heading).text.split(/[:،]/)[0]!.trim();
  if(t.type==='list'&&current){
   const items=(t as Tokens.List).items.flatMap(item=>{const link=firstLink(item.tokens);return link?[{label:plain(link.text),href:rewrite(path,link.href,set)}]:[];});
   if(items.length)groups.push({title:current,items});
  }
 }
 return groups;
}

function sideList(lang:Lang,groups:Group[],url:string){
 const c=content[lang];
 const item=(label:string,href:string)=>{
  const external=/^https?:/.test(href);
  return `<li><a href="${esc(href)}"${href===url?' aria-current="page"':''}${external?' rel="noopener noreferrer"':''}>${esc(label)}${external?`${icon('external','ext')}<span class="sr-only"> ${esc(c.onGithub)}</span>`:''}</a></li>`;
 };
 return `<ul>${item(c.overview,docPage(lang,''))}</ul>${groups.map(g=>`<p class="nav-head">${esc(g.title)}</p><ul>${g.items.map(i=>item(i.label,i.href)).join('')}</ul>`).join('')}`;
}

export function docs():Page[]{
 const all=sources();
 const set=new Map(all.map(s=>[s.path,s]));
 const nav={ar:navigation('ar',set),en:navigation('en',set)};
 return all.map(source=>{
  const lang=source.lang,c=content[lang];
  const url=docPage(lang,source.slug);
  const otherLang:Lang=lang==='ar'?'en':'ar';
  const other=docPage(otherLang,source.slug);
  const {html,title,description,ids}=markdown(source,read(source.path),set);
  const list=sideList(lang,nav[lang],url);
  const page=`${head(lang,{title:`${title} | ${c.docsTitle}`,description:description||c.description,path:url,alternates:lang==='ar'?{ar:url,en:other}:{ar:other,en:url},type:'article'})}
<body class="docs">
${topbar(lang,{sectionBase:homeOf(lang),other,current:'docs'})}
<div class="wrap docs-layout">
<details class="docs-nav-compact"><summary>${esc(c.contents)}</summary><nav aria-label="${esc(c.docsNav)}">${list}</nav></details>
<aside class="docs-nav"><nav aria-label="${esc(c.docsNav)}">${list}</nav></aside>
<main id="main" class="prose">
${html}
<p class="edit"><a href="${repo}/edit/main/${source.path}" rel="noopener noreferrer">${icon('edit')}${esc(c.edit)}</a></p>
</main>
</div>
${footer(lang)}
${pageData({copy:c.copy,copied:c.copied})}
</body>
</html>`;
  return {source,url,other,title,html:page,ids};
 });
}

// The diagrams the pages show, copied next to them.
export const diagrams=()=>readdirSync(new URL('docs/diagrams',ROOT)).filter(f=>f.endsWith('.svg')).map(f=>`docs/diagrams/${f}`);
export const repoFile=(path:string)=>new URL(path,ROOT);

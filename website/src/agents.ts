import {Marked} from 'marked';
import GithubSlugger from 'github-slugger';
import {readFileSync} from 'node:fs';
import {content,docPage,repo,type Lang} from './content';
import {SITE,esc} from './layout';
import {rewrite,sources,type Page} from './docs';

// What the site offers search tools and AI agents besides the HTML pages: llms.txt
// (llmstxt.org) with a link to every docs page as Markdown, llms-full.txt with the
// English docs in one file, and an Atom feed of the changelog per language.

const ROOT=new URL('../../',import.meta.url);
const read=(path:string)=>readFileSync(new URL(path,ROOT),'utf8');

export function llms(pages:Page[]){
 const en=pages.filter(p=>p.source.lang==='en');
 const group=(prefix:string)=>en.filter(p=>p.source.slug.startsWith(prefix)).map(p=>`- [${p.title}](${SITE}${p.url}index.md): ${p.description}`).join('\n');
 const home=en.find(p=>p.source.slug==='')!,changelog=en.find(p=>p.source.slug==='changelog')!;
 return `# Sbarbase

> Sbarbase runs many Supabase projects on one server you own. Each environment gets the original upstream services (PostgreSQL, Auth, PostgREST, Storage, Realtime, Studio) with its own database, logins and keys, administered from a console, a Supabase-compatible /v1 API and a hosted MCP endpoint. Open source and in development; not production ready.

Every page below is Markdown; the same page as HTML is the URL without index.md. Arabic pages are under ${SITE}/docs/ and English pages under ${SITE}/en/docs/. Source code: ${repo}.

## Start here

- [Documentation map](${SITE}${home.url}index.md): ${home.description}
- [Changelog](${SITE}${changelog.url}index.md): every release, newest first.

## Explain

${group('explain/')}

## Guides

${group('guides/')}

## Reference

${group('reference/')}

## Optional

- [All English docs in one file](${SITE}/llms-full.txt)
- [Changelog feed](${SITE}/en/docs/changelog/feed.xml)
`;
}

export function llmsFull(pages:Page[]){
 return pages.filter(p=>p.source.lang==='en').map(p=>`<!-- ${SITE}${p.url} -->\n\n${p.markdown.trim()}\n`).join('\n\n');
}

// One entry per released version: its heading, date and the notes as HTML with absolute links.
export function changelogFeed(lang:Lang,updated:string){
 const path=lang==='ar'?'CHANGELOG.ar.md':'CHANGELOG.md';
 const set=new Map(sources().map(s=>[s.path,s]));
 const page=`${SITE}${docPage(lang,'changelog')}`;
 const slugger=new GithubSlugger();
 const sections=read(path).split(/^## /m).slice(1);
 const entries=sections.flatMap(section=>{
  const [heading,...body]=section.split('\n');
  const anchor=slugger.slug(heading!.replace(/[[\]]/g,''));
  const m=heading!.match(/\[(\d+\.\d+\.\d+)\][^\d]*(\d{4}-\d{2}-\d{2})/);
  if(!m)return [];
  const md=new Marked({gfm:true});
  md.use({walkTokens(token){
   if(token.type==='link'||token.type==='image'){const r=rewrite(path,token.href,set);token.href=r.startsWith('/')?`${SITE}${r}`:r;}
  }});
  const html=(md.parse(body.join('\n')) as string).trim();
  return [{version:m[1]!,date:m[2]!,heading:heading!.replace(/[[\]]/g,''),anchor,html}];
 });
 const title=lang==='ar'?'سجل تغييرات صباربيز':'Sbarbase changelog';
 return `<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="${lang}">
<title>${esc(title)}</title>
<id>${page}</id>
<link rel="alternate" type="text/html" href="${page}"/>
<link rel="self" type="application/atom+xml" href="${page}feed.xml"/>
<updated>${updated}T00:00:00Z</updated>
<author><name>Sbarbase</name><uri>${repo}</uri></author>
${entries.map(e=>`<entry>
<title>${esc(`Sbarbase ${e.version}`)}</title>
<id>${page}#${encodeURIComponent(e.anchor)}</id>
<link rel="alternate" type="text/html" href="${page}#${encodeURIComponent(e.anchor)}"/>
<updated>${e.date}T00:00:00Z</updated>
<content type="html">${esc(e.html)}</content>
</entry>`).join('\n')}
</feed>
`;
}

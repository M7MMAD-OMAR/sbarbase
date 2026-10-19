import {content,links,type Lang} from './content';

// The page chrome shared by the home page and the docs pages: the head, the top
// bar and the footer. Each page passes its own URL, title and language switch.

export const SITE='https://base.sbarah.com';

export const esc=(s:string)=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

const icons:Record<string,string>={
 arrow:'M5 12h14m-6-6 6 6-6 6',
 down:'M12 5v14m-6-6 6 6 6-6',
 github:'M9 19c-5 1-5-2-7-2m14 5v-4c0-1 0-2-1-3 4 0 7-2 7-6 0-2-1-3-2-4 0-1 0-2-1-3-2 0-3 1-4 1-2-1-5-1-7 0-1 0-2-1-4-1-1 1-1 2-1 3-1 1-2 2-2 4 0 4 3 6 7 6-1 1-1 2-1 3v4',
 play:'m8 5 11 7-11 7Z',
 pause:'M8 5v14M16 5v14',
 replay:'M4 12a8 8 0 1 0 3-6.2M4 4v4h4',
 copy:'M9 9h11v11H9zM5 15H4V4h11v1',
 sun:'M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7 1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2-1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
 moon:'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5',
 book:'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21V5',
 edit:'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
 external:'M14 4h6v6m0-6-9 9M18 14v5H5V6h5',
};
export const icon=(name:string,cls='')=>`<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name]}"/></svg>`;

const brandMark=`<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="8" fill="#E06A4B"/><path d="M14 27c-1-8-1-15 2-17 3 2 3 9 2 17zM14 20c-3 0-4-2-4-5 0-1 2-1 2 0 0 2 1 3 2 3zM18 17c2 0 3-1 3-4 0-1 2-1 2 0 0 4-2 6-5 6z" fill="#FFFBF1"/></svg>`;

export const homeOf=(lang:Lang)=>lang==='ar'?'/':'/en/';

// path is the page's own path on the site; alternates are the same page in each language.
export function head(lang:Lang,o:{title:string,description:string,path:string,alternates:Record<Lang,string>,type?:string,extra?:string}){
 const url=`${SITE}${o.path}`;
 return `<!doctype html>
<html lang="${content[lang].lang}" dir="${content[lang].dir}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#F7F5F0" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#131A2A" media="(prefers-color-scheme: dark)">
<title>${esc(o.title)}</title>
<meta name="description" content="${esc(o.description)}">
<link rel="canonical" href="${url}">
<link rel="alternate" hreflang="ar" href="${SITE}${o.alternates.ar}">
<link rel="alternate" hreflang="en" href="${SITE}${o.alternates.en}">
<link rel="alternate" hreflang="x-default" href="${SITE}${o.alternates.ar}">
<meta property="og:type" content="${o.type??'website'}"><meta property="og:title" content="${esc(o.title)}"><meta property="og:description" content="${esc(o.description)}"><meta property="og:image" content="${SITE}/assets/social.jpg"><meta property="og:url" content="${url}"><meta property="og:locale" content="${lang==='ar'?'ar':'en_US'}"><meta name="twitter:card" content="summary_large_image">
${o.extra??''}<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="preload" href="/assets/${lang==='ar'?'arabic':'latin'}.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/style.css">
<script src="/app.js" type="module"></script>
</head>`;
}

// sectionBase is '' on the home page, so its menu links stay in-page anchors, and
// the home path on every other page. other is the same page in the other language.
export function topbar(lang:Lang,o:{sectionBase:string,other:string,current?:'docs'}){
 const c=content[lang],home=homeOf(lang);
 const docs=`<a href="${links(lang).docs}"${o.current==='docs'?' aria-current="page" class="current"':''}>${esc(c.docs)}</a>`;
 return `<a class="skip" href="#main">${c.skip}</a>
<header class="topbar"><div class="wrap topbar-row">
<a class="brand" href="${home}">${brandMark}<span>sbarbase</span></a>
<nav aria-label="${c.menu}">${c.nav.map(([label,id])=>`<a href="${o.sectionBase}#${id}">${esc(label)}</a>`).join('')}${o.sectionBase?docs:''}</nav>
<div class="top-actions">
<button type="button" class="round" id="theme" aria-label="${esc(c.themeLabel)}" aria-pressed="false">${icon('sun','sun')}${icon('moon','moon')}</button>
<a class="lang" href="${o.other}" lang="${lang==='ar'?'en':'ar'}" hreflang="${lang==='ar'?'en':'ar'}">${c.language}</a>
<a class="round" href="${links(lang).repo}" aria-label="${esc(c.source)}" rel="noopener noreferrer">${icon('github')}</a>
</div></div></header>`;
}

export function footer(lang:Lang){
 const c=content[lang],l=links(lang),home=homeOf(lang);
 return `<footer class="footer"><div class="wrap">
<a class="brand" href="${home}">${brandMark}<span>sbarbase</span></a>
<p class="footer-links"><a href="${l.docs}">${c.docs}</a><a href="${l.security}">${c.security}</a><a href="${l.license}">${c.license}</a><a href="https://sbarah.com">${c.home}</a></p>
<p class="fine">${esc(c.trademark)}</p>
</div></footer>`;
}

export const pageData=(data:unknown)=>`<script id="page-data" type="application/json">${JSON.stringify(data).replaceAll('<','\\u003c')}</script>`;

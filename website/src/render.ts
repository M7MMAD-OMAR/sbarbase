import {content,links,command,rules,type Lang} from './content';

// Paper cutout pages: torn sheets, tape and hand-lettered headings, with every
// technical drawing built here as inline SVG so it stays sharp, translatable and
// mirrored for Arabic (flows read right to left there). No external assets
// beyond the self-hosted fonts; the CSP allows nothing else.

const esc=(s:string)=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const lines=(s:string)=>esc(s).replaceAll('\n','<br>');

const icons:Record<string,string>={
 arrow:'M5 12h14m-6-6 6 6-6 6',
 github:'M9 19c-5 1-5-2-7-2m14 5v-4c0-1 0-2-1-3 4 0 7-2 7-6 0-2-1-3-2-4 0-1 0-2-1-3-2 0-3 1-4 1-2-1-5-1-7 0-1 0-2-1-4-1-1 1-1 2-1 3-1 1-2 2-2 4 0 4 3 6 7 6-1 1-1 2-1 3v4',
 check:'m5 12 4 4L19 6',
 play:'m8 5 11 7-11 7Z',
 pause:'M8 5v14M16 5v14',
 replay:'M4 12a8 8 0 1 0 3-6.2M4 4v4h4',
 copy:'M9 9h11v11H9zM5 15H4V4h11v1',
 sun:'M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7 1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2-1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
 moon:'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5',
 book:'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21V5',
};
const icon=(name:string,cls='')=>`<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name]}"/></svg>`;

// Shared SVG definitions: the torn paper edge, the hand wobble for drawings, and
// arrowheads. Referenced by id from CSS (filter:url(#torn)) and from every figure.
const defs=`<svg class="defs" width="0" height="0" aria-hidden="true" focusable="false"><defs>
<filter id="torn" x="-3%" y="-3%" width="106%" height="106%"><feTurbulence type="fractalNoise" baseFrequency="0.045" numOctaves="3" seed="4" result="n"/><feDisplacementMap in="SourceGraphic" in2="n" scale="11" xChannelSelector="R" yChannelSelector="G" result="d"/><feMorphology in="d" operator="dilate" radius="3" result="rim"/><feFlood flood-color="#FFFDF7"/><feComposite in2="rim" operator="in" result="rimw"/><feDropShadow in="rimw" dx="0" dy="3" stdDeviation="2.4" flood-color="#2B1E10" flood-opacity=".22" result="rs"/><feMerge><feMergeNode in="rs"/><feMergeNode in="d"/></feMerge></filter>
<filter id="wob" x="-4%" y="-4%" width="108%" height="108%"><feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="9" result="n"/><feDisplacementMap in="SourceGraphic" in2="n" scale="3" xChannelSelector="R" yChannelSelector="G"/></filter>
<marker id="ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M1 1 9 5 1 9" fill="none" stroke="#2A2632" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>
<marker id="ahc" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M1 1 9 5 1 9" fill="none" stroke="#E06A4B" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs></svg>`;

// The mascot: a paper cactus (sabbar) in a pot, waving. Eyes blink and the arm
// waves in CSS; both stop under prefers-reduced-motion.
function cactus(label:string,cls='mascot'){
 return `<svg class="${cls}" viewBox="0 0 320 380" role="img" aria-label="${esc(label)}">
<g filter="url(#wob)">
<path d="M70 300 250 300 232 372 88 372Z" fill="#B7734A" stroke="#FFFDF7" stroke-width="5"/>
<rect x="60" y="286" width="200" height="26" rx="4" fill="#C98458" stroke="#FFFDF7" stroke-width="5"/>
<path d="M120 290C112 180 118 110 160 96 202 110 208 180 200 290Z" fill="#3E9E6E" stroke="#FFFDF7" stroke-width="5"/>
<path d="M122 214C92 214 78 196 78 160 78 146 98 146 98 160 98 184 106 192 124 192Z" fill="#3E9E6E" stroke="#FFFDF7" stroke-width="5"/>
<g class="wave"><path d="M198 196C222 196 232 184 232 150 232 136 252 136 252 150 252 196 232 218 198 218Z" fill="#3E9E6E" stroke="#FFFDF7" stroke-width="5"/></g>
<g fill="#EC9197" stroke="#FFFDF7" stroke-width="3"><circle cx="160" cy="86" r="12"/><circle cx="146" cy="96" r="11"/><circle cx="174" cy="96" r="11"/><circle cx="152" cy="110" r="10"/><circle cx="168" cy="110" r="10"/></g>
<circle cx="160" cy="100" r="7" fill="#F0C23E"/>
</g>
<g stroke="#DDF2E6" stroke-width="2.4" stroke-linecap="round"><path d="M140 140l-6-4M180 146l6-4M136 250l-6 2M186 256l6 2M160 262v7M92 166l-6-3M238 158l6-3"/></g>
<g class="eyes"><ellipse cx="146" cy="192" rx="5" ry="5" fill="#2A2632"/><ellipse cx="174" cy="192" rx="5" ry="5" fill="#2A2632"/></g>
<circle cx="136" cy="208" r="8" fill="#F29BA0" opacity=".85"/><circle cx="184" cy="208" r="8" fill="#F29BA0" opacity=".85"/>
<path d="M150 210q10 10 20 0" fill="none" stroke="#2A2632" stroke-width="3" stroke-linecap="round"/>
</svg>`;
}

const brandMark=`<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M9 26h14l-2 5H11z" fill="#B7734A"/><path d="M13 26c-1-8-1-15 3-17 4 2 4 9 3 17z" fill="#3E9E6E"/><path d="M13 19c-3 0-4-2-4-5 0-1 2-1 2 0 0 2 1 3 2 3zM19 16c2 0 3-1 3-4 0-1 2-1 2 0 0 4-2 6-5 6z" fill="#3E9E6E"/><circle cx="16" cy="8" r="2.4" fill="#EC9197"/></svg>`;

// The request walkthrough. Coordinates are written left to right and mirrored for
// Arabic, so the same geometry reads in the reader's direction.
function requestFigure(lang:Lang){
 const c=content[lang],d=c.diagram,l=c.labels,W=900;
 const X=(x:number,w=0)=>lang==='ar'?W-x-w:x;
 const box=(x:number,y:number,w:number,h:number,fill:string,stroke='#2A2632',extra='')=>`<rect x="${X(x,w)}" y="${y}" width="${w}" height="${h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="1.8" ${extra}/>`;
 const text=(x:number,y:number,s:string,cls='')=>`<text x="${X(x)}" y="${y}" class="${cls}">${esc(s)}</text>`;
 const p=(pts:string)=>pts.replace(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g,(_m,x,y)=>`${X(Number(x))} ${y}`);
 return `<svg class="figure request-figure" viewBox="0 0 ${W} 370" role="img" aria-labelledby="req-title">
<title id="req-title">${esc(c.steps.map(([t])=>t).join('. '))}</title>
<g filter="url(#wob)">
${box(20,140,150,82,'#FFFBF1')}
${box(236,88,230,184,'#F6C7B6','#E06A4B')}
${box(530,66,150,56,'#BFE3D8')}
${box(530,146,150,56,'#BFE3D8')}
${box(530,248,150,64,'#F6C7B6','#E06A4B')}
${box(726,34,160,300,'#F6C7B6','#E06A4B')}
${box(742,82,128,96,'#F0C23E')}
${box(742,196,128,40,'#EDE4D0','#2A2632','stroke-dasharray="5 4"')}
${box(742,250,128,40,'#EDE4D0','#2A2632','stroke-dasharray="5 4"')}
</g>
<g class="fig-text">
${text(95,178,d.app,'strong')}${text(95,202,'supabase-js','mono')}
${text(351,118,l.gateway,'strong')}
<g class="checks">${['1','2','3'].map((n,i)=>`<g class="check" data-check="${i}"><circle cx="${X(262)}" cy="${150+i*34}" r="9" class="tick-dot"/><path d="M${X(257)} ${150+i*34} l3 4 7-8" class="tick"/>${text(282,155+i*34,d.checks.split(/,\s*|،\s*/)[i]??n,'small start')}</g>`).join('')}</g>
${text(605,99,`${l.auth} · ${d.own}`)}
${text(605,179,`${l.rest} · ${d.own}`)}
${text(605,276,l.store,'strong')}${text(605,298,d.shared,'small')}
${text(806,62,l.engine,'small strong')}
${text(806,124,`${l.db} · ${d.own}`,'strong')}${text(806,148,d.login,'small')}
${text(806,221,d.other,'small')}${text(806,275,d.other,'small')}
</g>
<g class="wires" fill="none" stroke="#2A2632" stroke-width="1.8" stroke-linecap="round">
<path id="w1" d="${p('M170 181 L236 181')}" marker-end="url(#ah)"/>
<path id="w2" d="${p('M466 170 C500 170 500 174 530 174')}" marker-end="url(#ah)"/>
<path d="${p('M466 150 C500 150 500 94 530 94')}" marker-end="url(#ah)"/>
<path id="w3" d="${p('M680 174 C706 174 712 130 742 130')}" marker-end="url(#ah)"/>
<path d="${p('M680 94 C706 94 712 118 742 118')}" marker-end="url(#ah)"/>
<path d="${p('M466 240 C500 240 500 280 530 280')}" marker-end="url(#ah)"/>
<path d="${p('M680 280 C706 280 712 170 742 160')}" stroke-dasharray="4 5" marker-end="url(#ah)"/>
<path d="${p('M351 272 L351 316')}" stroke="#E06A4B" marker-end="url(#ahc)"/>
</g>
${text(351,346,c.refused,'refused small')}
${text(605,332,d.files,'small muted')}
<circle class="packet" r="9" cx="${X(170)}" cy="181"/>
</svg>`;
}

// Today versus Sbarbase, drawn in HTML so it reflows on a phone. Four columns are
// rendered and CSS shows as many as the slider says.
function comparison(lang:Lang){
 const c=content[lang],l=c.labels;
 const stack=(n:number)=>`<div class="stack-col" data-col="${n}"><span class="chip lilac">${l.studio}</span><span class="chip teal">${l.auth}</span><span class="chip teal">${l.rest}</span><span class="chip pink">${l.store}</span><span class="chip mustard tall">${l.pg}</span><span class="chip dashed">${l.logs}</span><small>${l.project} ${n}</small></div>`;
 const env=(n:number)=>`<div class="env-col" data-col="${n}"><small>${l.env} ${n}</small><span class="chip teal">${l.auth}</span><span class="chip teal">${l.rest}</span></div>`;
 return `<div class="compare" data-count="2" data-view="ours">
<div class="compare-controls">
 <div class="segmented" role="group" aria-label="${esc(c.compareTitle)}"><button type="button" data-view="today" aria-pressed="false">${c.compareToday}</button><button type="button" data-view="ours" aria-pressed="true">${c.compareOurs}</button></div>
 <label class="slider"><span>${c.compareSlider}: <output id="env-count">2</output></span><input id="env-range" type="range" min="1" max="${rules.maxEnvironments}" value="2"></label>
</div>
<div class="compare-stage">
 <div class="compare-today" aria-hidden="true"><h4>${c.compareFull}</h4><div class="stacks">${[1,2,3,4].map(stack).join('')}</div><p class="note">${c.compareFullNote}</p></div>
 <div class="compare-ours">
  <span class="bar coral">${l.gateway}</span>
  <div class="envs">${[1,2,3,4].map(env).join('')}</div>
  <div class="bar coral engine"><span>${l.engine}</span><div class="dbs">${[1,2,3,4].map(n=>`<span class="chip mustard" data-col="${n}">${l.db} ${n}</span>`).join('')}</div></div>
  <span class="bar coral">${l.storage}</span>
  <span class="chip lilac dashed planned"><bdi>${l.studio}</bdi>: ${l.planned}</span>
  <p class="note">${c.compareSharedNote}</p>
 </div>
</div>
<div class="compare-numbers" aria-live="polite"><p><b id="n-containers">${rules.systemContainers+2*rules.perEnvironmentContainers}</b> ${c.compareContainers}</p><p><b id="n-memory">${rules.systemMib+2*rules.perEnvironmentMib}</b> ${c.compareMemory}</p></div>
<p class="caption">${c.compareCaption}</p>
</div>`;
}

function hierarchy(lang:Lang){
 const c=content[lang],t=c.tree;
 const env=(id:string,owner:string,name:string)=>`<span class="env-chip" data-env="${id}" data-owner="${owner}">${esc(owner)} · ${esc(name)}</span>`;
 return `<div class="hier" data-level="2">
<div class="tree" role="group" aria-label="${esc(c.hierLabel)}">
 ${[[t.clientA,[[t.shop,[t.prod,t.staging]]]],[t.clientB,[[t.blog,[t.prod]]]]].map(([client,projects])=>`<div class="tree-client"><button type="button" class="node client" data-level="0">${esc(client as string)}</button>${(projects as [string,string[]][]).map(([project,envs])=>`<div class="tree-project"><button type="button" class="node project" data-level="1">${esc(project)}</button><div class="tree-envs">${envs.map(e=>`<button type="button" class="node env" data-level="2">${esc(e)}</button>`).join('')}</div></div>`).join('')}</div>`).join('')}
</div>
<div class="servers">
 <button type="button" class="server" data-level="3"><span class="server-name">${t.server}</span><span class="engine-line">PostgreSQL</span><span class="server-envs" id="server-1">${env('a',`${t.shop}`,t.prod)}${env('b',`${t.shop}`,t.staging)}${env('c',`${t.blog}`,t.prod)}</span></button>
 <div class="server planned-box"><span class="server-name">${t.server2} · ${c.labels.planned}</span><span class="server-envs" id="server-2"></span></div>
 <button type="button" class="button ghost" id="move">${c.moveAction}</button>
</div>
<div class="level-card" aria-live="polite"><h4 id="level-name">${c.levels[2]![0]}</h4><p id="level-text">${c.levels[2]![1]}</p><p class="note">${c.moveNote}</p></div>
</div>`;
}

function isolation(lang:Lang){
 const c=content[lang];
 const part=([name,kind,_note]:[string,string,string],i:number)=>`<button type="button" class="part ${kind}" data-part="${i}" aria-pressed="${i===0}">${esc(name)}</button>`;
 return `<div class="iso">
<div class="iso-cols"><div><h4><span class="key teal"></span>${c.isoOwn}</h4><div class="parts">${c.parts.map((p,i)=>p[1]==='own'?part(p,i):'').join('')}</div></div>
<div><h4><span class="key coral"></span>${c.isoShared}</h4><div class="parts">${c.parts.map((p,i)=>p[1]==='shared'?part(p,i):'').join('')}</div></div></div>
<div class="iso-note note-card" aria-live="polite"><strong id="part-name">${esc(c.parts[0]![0])}</strong><p id="part-text">${esc(c.parts[0]![2])}</p><a href="${links.threat}">${c.trustText}${icon('arrow','flip')}</a></div>
</div>`;
}

function recovery(lang:Lang){
 const c=content[lang];
 return `<div class="rec" data-step="0">
<ol class="rec-track">${c.recSteps.map(([t],i)=>`<li><button type="button" data-rec="${i}" aria-pressed="${i===0}"><span class="stamp">${i+1}</span><span>${esc(t)}</span></button></li>`).join('')}</ol>
<div class="rec-lane" aria-hidden="true"><span class="parcel">${icon('copy')}</span></div>
<p class="rec-text note-card" id="rec-text" aria-live="polite">${esc(c.recSteps[0]![1])}</p>
<p class="caption">${c.recLimit}</p>
</div>`;
}

export function render(lang:Lang){
 const c=content[lang];
 const home=lang==='ar'?'/':'/en/';
 const url=`https://base.sbarah.com${home}`;
 const sheet=(id:string,cls:string,inner:string)=>`<section class="sheet ${cls}" id="${id}"><span class="tape one"></span><span class="tape two"></span><div class="sheet-body">${inner}</div></section>`;
 const heading=(label:string,title:string,text='')=>`<p class="eyebrow">${esc(label)}</p><h2>${lines(title)}</h2>${text?`<p class="lede">${esc(text)}</p>`:''}`;
 const data={steps:c.steps,levels:c.levels,parts:c.parts,recSteps:c.recSteps,play:c.play,pause:c.pause,copy:c.copy,copied:c.copied,moveAction:c.moveAction,moveBack:c.moveBack,rules};

 return `<!doctype html>
<html lang="${c.lang}" dir="${c.dir}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#F1E7D2" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1D2158" media="(prefers-color-scheme: dark)">
<title>${esc(c.title)}</title>
<meta name="description" content="${esc(c.description)}">
<link rel="canonical" href="${url}">
<link rel="alternate" hreflang="ar" href="https://base.sbarah.com/">
<link rel="alternate" hreflang="en" href="https://base.sbarah.com/en/">
<link rel="alternate" hreflang="x-default" href="https://base.sbarah.com/">
<meta property="og:type" content="website"><meta property="og:title" content="${esc(c.title)}"><meta property="og:description" content="${esc(c.description)}"><meta property="og:image" content="https://base.sbarah.com/assets/social.png"><meta property="og:url" content="${url}"><meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="preload" href="/assets/hand.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/assets/${lang==='ar'?'arabic':'latin'}.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/style.css">
<script src="/app.js" type="module"></script>
</head>
<body>
${defs}
<a class="skip" href="#main">${c.skip}</a>
<header class="topbar"><div class="wrap topbar-row">
<a class="brand" href="${home}">${brandMark}<span>sbarbase</span></a>
<nav aria-label="${c.menu}">${c.nav.map(([label,id])=>`<a href="#${id}">${esc(label)}</a>`).join('')}</nav>
<div class="top-actions">
<button type="button" class="round" id="theme" aria-label="${esc(c.themeLabel)}">${icon('sun','sun')}${icon('moon','moon')}</button>
<a class="lang" href="${c.languageUrl}" lang="${lang==='ar'?'en':'ar'}">${c.language}</a>
<a class="round" href="${links.repo}" aria-label="${esc(c.source)}" rel="noopener noreferrer">${icon('github')}</a>
</div></div></header>

<main id="main" class="wrap">

<section class="hero sheet mustard" id="top"><span class="tape one"></span><span class="tape two"></span><div class="sheet-body hero-grid">
<div class="hero-copy">
<p class="badge"><span class="dot"></span>${esc(c.badge)}</p>
<h1>${lines(c.headline)}</h1>
<p class="lede">${esc(c.intro)}</p>
<div class="actions"><a class="button primary" href="#start">${c.ctaStart}${icon('arrow','flip')}</a><a class="button" href="${links.docs}">${icon('book')}${c.ctaDocs}</a></div>
<p class="honest">${esc(c.heroHonest)}</p>
</div>
<div class="hero-art">
${cactus(c.mascot)}
<ul class="stickies">${c.heroNotes.map((n,i)=>`<li class="sticky s${i}">${esc(n)}</li>`).join('')}</ul>
</div>
</div></section>

${sheet('why','paper',`${heading(c.whyLabel,c.whyTitle,c.whyText)}
<div class="pains">${c.pains.map(([t,d,l,h],i)=>`<article class="pain p${i}"><h3>${esc(t)}</h3><p>${esc(d)}</p><a href="${h}" rel="noopener noreferrer">${esc(l)}${icon('arrow','flip')}</a></article>`).join('')}</div>
<div class="subhead"><p class="eyebrow">${esc(c.compareLabel)}</p><h3>${esc(c.compareTitle)}</h3></div>
${comparison(lang)}`)}

${sheet('how','teal',`${heading(c.howLabel,c.howTitle,c.howText)}
<figure class="walk" data-step="0">
<div class="figure-box">${requestFigure(lang)}</div>
<figcaption>
<ol class="walk-steps">${c.steps.map(([t,d],i)=>`<li data-walk="${i}"><button type="button" aria-pressed="${i===0}"><span class="stamp">${i+1}</span><span><b>${esc(t)}</b><span class="desc">${esc(d)}</span></span></button></li>`).join('')}</ol>
<div class="player"><button type="button" class="round" id="walk-play" aria-label="${c.pause}" aria-pressed="true">${icon('pause','pause')}${icon('play','play')}</button><label class="sr-only" for="walk-seek">${c.seek}</label><input id="walk-seek" type="range" min="0" max="11999" value="0"><button type="button" class="round" id="walk-replay" aria-label="${c.replay}">${icon('replay')}</button></div>
</figcaption>
</figure>
<p class="more"><a href="${links.architecture}">${c.docs}${icon('arrow','flip')}</a></p>`)}

${sheet('hierarchy','lilac',`${heading(c.hierLabel,c.hierTitle,c.hierText)}${hierarchy(lang)}`)}

${sheet('isolation','paper',`${heading(c.isoLabel,c.isoTitle,c.isoText)}${isolation(lang)}`)}

${sheet('recovery','pink',`${heading(c.recLabel,c.recTitle,c.recText)}${recovery(lang)}`)}

${sheet('proof','night',`${heading(c.proofLabel,c.proofTitle,c.proofText)}
<div class="stats">${c.proofStats.map(([v,t],i)=>`<div class="stat st${i}"><b>${esc(v)}</b><span>${esc(t)}</span></div>`).join('')}</div>
<details class="bugs"><summary>${esc(c.bugsTitle)}</summary><ol>${c.bugs.map(b=>`<li>${esc(b)}</li>`).join('')}</ol></details>
<p class="caption">${esc(c.proofNote)} <a href="${links.evidence}">${c.proofLink}${icon('arrow','flip')}</a></p>`)}

${sheet('roadmap','paper',`${heading(c.roadLabel,c.roadTitle)}
<ol class="road">${c.milestones.map(([t,d,s])=>`<li class="${s}"><span class="state">${c.states[s]}</span><h3>${esc(t)}</h3><p>${esc(d)}</p></li>`).join('')}</ol>
<p class="more"><a href="${links.roadmap}">${c.roadLabel}${icon('arrow','flip')}</a></p>`)}

${sheet('start','mustard',`${heading(c.startLabel,c.startTitle,c.startText)}
<div class="terminal"><div class="terminal-top"><span></span><span></span><span></span><button type="button" class="copy" id="copy" data-copy="${esc(command)}">${icon('copy')}<span>${c.copy}</span></button></div><pre dir="ltr"><code>${esc(command)}</code></pre></div>
<ul class="needs">${c.needs.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>
<p class="links">${c.startLinks.map(([t,h])=>`<a class="button" href="${h}">${esc(t)}${icon('arrow','flip')}</a>`).join('')}</p>`)}

${sheet('faq','paper',`<h2>${esc(c.faqTitle)}</h2><div class="faqs">${c.faqs.map(([q,a])=>`<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join('')}</div>`)}

<section class="cta">${cactus(c.mascot,'mascot small')}<h2>${lines(c.ctaTitle)}</h2><p>${esc(c.ctaText)}</p><a class="button primary" href="${links.repo}" rel="noopener noreferrer">${icon('github')}${c.cta}</a></section>
</main>

<footer class="wrap footer">
<a class="brand" href="${home}">${brandMark}<span>sbarbase</span></a>
<p>${esc(c.footerNote)}</p>
<p class="footer-links"><a href="${links.docs}">${c.docs}</a><a href="${links.security}">${c.security}</a><a href="${links.contributing}">CONTRIBUTING</a><a href="${links.license}">${c.license}</a><a href="https://sbarah.com">${c.home}</a></p>
<p class="fine">${esc(c.rights)}</p>
</footer>
<script id="page-data" type="application/json">${JSON.stringify(data).replaceAll('<','\\u003c')}</script>
</body>
</html>`;
}

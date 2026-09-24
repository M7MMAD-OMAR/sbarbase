import {content,links,commands,type Lang} from './content';

// Prerendered pages. The UI is plain and technical; only the two diagrams keep the
// hand-drawn wobble. Both diagrams are inline SVG written left to right and
// mirrored for Arabic, so they read in the reader's direction. No external assets
// beyond the self-hosted fonts; the CSP allows nothing else.

const esc=(s:string)=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

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
};
const icon=(name:string,cls='')=>`<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name]}"/></svg>`;

// Shared SVG definitions: the hand wobble for the diagrams, and arrowheads.
const defs=`<svg class="defs" width="0" height="0" aria-hidden="true" focusable="false"><defs>
<filter id="wob" x="-4%" y="-4%" width="108%" height="108%"><feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="9" result="n"/><feDisplacementMap in="SourceGraphic" in2="n" scale="3" xChannelSelector="R" yChannelSelector="G"/></filter>
<marker id="ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M1 1 9 5 1 9" fill="none" stroke="#2A2632" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>
<marker id="ahc" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M1 1 9 5 1 9" fill="none" stroke="#E06A4B" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs></svg>`;

const brandMark=`<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="8" fill="#E06A4B"/><path d="M14 27c-1-8-1-15 2-17 3 2 3 9 2 17zM14 20c-3 0-4-2-4-5 0-1 2-1 2 0 0 2 1 3 2 3zM18 17c2 0 3-1 3-4 0-1 2-1 2 0 0 4-2 6-5 6z" fill="#FFFBF1"/></svg>`;

// Mirroring helpers shared by both diagrams: X() flips a box or point for Arabic,
// p() flips every "x y" pair in a path.
const mirror=(lang:Lang,W:number)=>{
 const X=(x:number,w=0)=>lang==='ar'?W-x-w:x;
 const p=(pts:string)=>pts.replace(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g,(_m,x,y)=>`${X(Number(x))} ${y}`);
 const box=(x:number,y:number,w:number,h:number,fill:string,stroke='#2A2632',extra='')=>`<rect x="${X(x,w)}" y="${y}" width="${w}" height="${h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="1.8" ${extra}/>`;
 const text=(x:number,y:number,s:string,cls='')=>`<text x="${X(x)}" y="${y}" class="${cls}">${esc(s)}</text>`;
 return {X,p,box,text};
};

// The request walkthrough. Coordinates are written left to right and mirrored for
// Arabic, so the same geometry reads in the reader's direction.
function requestFigure(lang:Lang){
 const c=content[lang],d=c.diagram,l=c.labels,W=900;
 const {X,p,box,text}=mirror(lang,W);
 return `<svg class="figure request-figure" viewBox="0 0 ${W} 370" role="img" aria-labelledby="req-title">
<title id="req-title">${esc(c.steps.join(' '))}</title>
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
<g class="checks">${d.checks.split(/,\s*|،\s*/).map((label,i)=>`<g class="check" data-check="${i}"><circle cx="${X(262)}" cy="${150+i*34}" r="9" class="tick-dot"/><path d="M${X(257)} ${150+i*34} l3 4 7-8" class="tick"/>${text(282,155+i*34,label,'small start')}</g>`).join('')}</g>
${text(605,99,`${l.auth} · ${d.own}`)}
${text(605,179,`${l.rest} · ${d.own}`)}
${text(605,276,l.store,'strong')}${text(605,298,d.shared,'small')}
${text(806,62,'PostgreSQL','small strong')}
${text(806,110,l.db,'strong')}${text(806,134,d.own,'small')}${text(806,156,d.login,'small')}
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
${text(351,346,d.refused,'refused small')}
<circle class="packet" r="9" cx="${X(170)}" cy="181"/>
</svg>`;
}

// The hierarchy: clients own projects, projects hold environments, and on your
// server every environment is its own database inside one PostgreSQL engine.
function hierarchyFigure(lang:Lang){
 const t=content[lang].tree,W=900;
 const {p,box,text}=mirror(lang,W);
 const envs:[string,number][]=[[`${t.shop} · ${t.prod}`,64],[`${t.shop} · ${t.staging}`,116],[`${t.blog} · ${t.prod}`,200]];
 const dbY=[108,164,220];
 return `<svg class="figure hier-figure" viewBox="0 0 ${W} 310" role="img" aria-labelledby="hier-title">
<title id="hier-title">${esc(content[lang].hierAlt)}</title>
<g class="fig-text heads">${text(85,40,t.client,'small muted')}${text(250,40,t.project,'small muted')}${text(420,40,t.env,'small muted')}</g>
<g class="wires" fill="none" stroke="#2A2632" stroke-width="1.8" stroke-linecap="round">
<path d="${p('M150 110 L190 110')}"/><path d="${p('M150 220 L190 220')}"/>
<path d="${p('M310 110 C330 110 330 84 350 84')}"/><path d="${p('M310 110 C330 110 330 136 350 136')}"/><path d="${p('M310 220 L350 220')}"/>
${envs.map(([,y],i)=>`<path d="${p(`M490 ${y+20} C550 ${y+20} 560 ${dbY[i]!+22} 608 ${dbY[i]!+22}`)}" stroke-dasharray="4 5" marker-end="url(#ah)"/>`).join('')}
</g>
<g filter="url(#wob)">
${box(20,88,130,44,'#FFFBF1')}${box(20,198,130,44,'#FFFBF1')}
${box(190,88,120,44,'#FFFBF1')}${box(190,198,120,44,'#FFFBF1')}
${envs.map(([,y])=>box(350,y,140,40,'#BFE3D8')).join('')}
${box(560,20,320,276,'#EDE4D0','#2A2632','stroke-dasharray="6 5"')}
${box(584,66,272,214,'#F6C7B6','#E06A4B')}
${dbY.map(y=>box(610,y,220,44,'#F0C23E')).join('')}
</g>
<g class="fig-text">
${text(85,115,t.clientA,'strong')}${text(85,225,t.clientB,'strong')}
${text(250,115,t.shop)}${text(250,225,t.blog)}
${envs.map(([s,y])=>text(420,y+25,s.split(' · ')[1]!,'small')).join('')}
${text(720,48,t.server,'strong')}
${text(720,92,t.engine,'small strong')}
${envs.map(([s],i)=>text(720,dbY[i]!+27,`${t.db} · ${s}`,'small')).join('')}
</g>
</svg>`;
}

// The same hierarchy stacked for phones: the tree on top, your server below, so
// the whole picture fits a narrow screen without scrolling sideways.
function hierarchyCompact(lang:Lang){
 const t=content[lang].tree,W=460;
 const {p,box,text}=mirror(lang,W);
 const envs:[string,string,number][]=[[t.shop,t.prod,44],[t.shop,t.staging,96],[t.blog,t.prod,180]];
 const dbY=[358,414,470];
 return `<svg class="figure hier-compact" viewBox="0 0 ${W} 552" role="img" aria-labelledby="hier-title-m">
<title id="hier-title-m">${esc(content[lang].hierAlt)}</title>
<g class="fig-text heads">${text(70,26,t.client,'small muted')}${text(200,26,t.project,'small muted')}${text(370,26,t.env,'small muted')}</g>
<g class="wires" fill="none" stroke="#2A2632" stroke-width="1.8" stroke-linecap="round">
<path d="${p('M130 90 L145 90')}"/><path d="${p('M130 200 L145 200')}"/>
<path d="${p('M255 90 C272 90 272 64 290 64')}"/><path d="${p('M255 90 C272 90 272 116 290 116')}"/><path d="${p('M255 200 L290 200')}"/>
<path d="${p('M230 234 L230 274')}" marker-end="url(#ah)"/>
</g>
<g filter="url(#wob)">
${box(10,68,120,44,'#FFFBF1')}${box(10,178,120,44,'#FFFBF1')}
${box(145,68,110,44,'#FFFBF1')}${box(145,178,110,44,'#FFFBF1')}
${envs.map(([,,y])=>box(290,y,160,40,'#BFE3D8')).join('')}
${box(10,282,440,262,'#EDE4D0','#2A2632','stroke-dasharray="6 5"')}
${box(30,322,400,206,'#F6C7B6','#E06A4B')}
${dbY.map(y=>box(60,y,340,44,'#F0C23E')).join('')}
</g>
<g class="fig-text">
${text(70,95,t.clientA,'strong')}${text(70,205,t.clientB,'strong')}
${text(200,95,t.shop)}${text(200,205,t.blog)}
${envs.map(([,e,y])=>text(370,y+25,e,'small')).join('')}
${text(230,308,t.server,'strong')}
${text(230,346,t.engine,'small strong')}
${envs.map(([proj,e],i)=>text(230,dbY[i]!+27,`${t.db} · ${proj} · ${e}`,'small')).join('')}
</g>
</svg>`;
}

export function render(lang:Lang){
 const c=content[lang];
 const home=lang==='ar'?'/':'/en/';
 const url=`https://base.sbarah.com${home}`;
 const data={steps:c.steps,play:c.play,pause:c.pause,copy:c.copy,copied:c.copied};
 const heading=(id:string,label:string,title:string,text='')=>`<p class="eyebrow">${esc(label)}</p><h2 id="${id}-title">${esc(title)}</h2>${text?`<p class="lede">${esc(text)}</p>`:''}`;

 return `<!doctype html>
<html lang="${c.lang}" dir="${c.dir}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#F7F5F0" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#131A2A" media="(prefers-color-scheme: dark)">
<title>${esc(c.title)}</title>
<meta name="description" content="${esc(c.description)}">
<link rel="canonical" href="${url}">
<link rel="alternate" hreflang="ar" href="https://base.sbarah.com/">
<link rel="alternate" hreflang="en" href="https://base.sbarah.com/en/">
<link rel="alternate" hreflang="x-default" href="https://base.sbarah.com/">
<meta property="og:type" content="website"><meta property="og:title" content="${esc(c.title)}"><meta property="og:description" content="${esc(c.description)}"><meta property="og:image" content="https://base.sbarah.com/assets/social.jpg"><meta property="og:url" content="${url}"><meta property="og:locale" content="${lang==='ar'?'ar':'en_US'}"><meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
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
<button type="button" class="round" id="theme" aria-label="${esc(c.themeLabel)}" aria-pressed="false">${icon('sun','sun')}${icon('moon','moon')}</button>
<a class="lang" href="${c.languageUrl}" lang="${lang==='ar'?'en':'ar'}" hreflang="${lang==='ar'?'en':'ar'}">${c.language}</a>
<a class="round" href="${links.repo}" aria-label="${esc(c.source)}" rel="noopener noreferrer">${icon('github')}</a>
</div></div></header>

<main id="main">

<section class="hero" id="top"><div class="wrap hero-grid">
<div class="hero-copy">
<p class="badge"><span class="dot" aria-hidden="true"></span>${esc(c.badge)}</p>
<h1>${esc(c.headline)}</h1>
<p class="lede">${esc(c.intro)}</p>
<div class="actions"><a class="button primary" href="#install">${icon('down')}${c.ctaInstall}</a><a class="button" href="${links.docs}" rel="noopener noreferrer">${icon('book')}${c.ctaDocs}</a></div>
</div>
<figure class="video">
<div class="video-frame"><video id="explainer" src="/media/explainer.mp4" poster="/media/explainer-poster.jpg" muted playsinline controls preload="metadata" aria-label="${esc(c.videoLabel)}"><track kind="captions" src="/media/explainer.ar.vtt" srclang="ar" label="العربية"${lang==='ar'?' default':''}><track kind="captions" src="/media/explainer.en.vtt" srclang="en" label="English"${lang==='en'?' default':''}></video></div>
<figcaption>${esc(c.videoCaption)}</figcaption>
</figure>
</div></section>

<section class="section" id="hierarchy" aria-labelledby="hierarchy-title"><div class="wrap">
${heading('hierarchy',c.hierLabel,c.hierTitle,c.hierText)}
<div class="figure-box hier-box">${hierarchyFigure(lang)}${hierarchyCompact(lang)}</div>
</div></section>

<section class="section" id="how" aria-labelledby="how-title"><div class="wrap">
${heading('how',c.howLabel,c.howTitle)}
<figure class="walk" data-step="0">
<div class="figure-box">${requestFigure(lang)}</div>
<ul class="legend"><li><span class="key coral"></span>${esc(c.legend.shared)}</li><li><span class="key teal"></span>${esc(c.legend.own)}</li><li><span class="key mustard"></span>${esc(c.legend.db)}</li></ul>
<figcaption>
<ol class="walk-steps">${c.steps.map((s,i)=>`<li data-walk="${i}"><button type="button" aria-pressed="${i===0}"><span class="num">${i+1}</span><span>${esc(s)}</span></button></li>`).join('')}</ol>
<div class="player"><button type="button" class="round" id="walk-play" aria-label="${c.pause}" aria-pressed="true">${icon('pause','pause')}${icon('play','play')}</button><label class="sr-only" for="walk-seek">${c.seek}</label><input id="walk-seek" type="range" min="0" max="11999" value="0"><button type="button" class="round" id="walk-replay" aria-label="${c.replay}">${icon('replay')}</button></div>
</figcaption>
</figure>
</div></section>

<section class="section" id="install" aria-labelledby="install-title"><div class="wrap">
${heading('install',c.installLabel,c.installTitle)}
<ul class="needs" aria-label="${esc(c.needsLabel)}">${c.needs.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>
<ol class="install">${commands.map((cmd,i)=>{const [title,note]=c.installSteps[i]!;return `<li><div class="step-head"><span class="num">${i+1}</span><h3>${esc(title)}</h3></div>${note?`<p class="note">${esc(note)}</p>`:''}<div class="cmd"><pre dir="ltr"><code>${esc(cmd)}</code></pre><button type="button" class="copy" data-copy="${esc(cmd)}" aria-label="${esc(`${c.copyLabel} ${i+1}`)}">${icon('copy')}<span>${c.copy}</span></button></div></li>`;}).join('')}</ol>
<p class="links"><a class="button primary" href="${links.quickstart}" rel="noopener noreferrer">${c.installLinks.quickstart}${icon('arrow','flip')}</a><a class="button" href="${links.docs}" rel="noopener noreferrer">${icon('book')}${c.installLinks.docs}</a><a class="button" href="${links.server}" rel="noopener noreferrer">${c.installLinks.server}</a><a class="button" href="${links.repo}" rel="noopener noreferrer">${icon('github')}${c.installLinks.repo}</a></p>
</div></section>
</main>

<footer class="footer"><div class="wrap">
<a class="brand" href="${home}">${brandMark}<span>sbarbase</span></a>
<p class="footer-links"><a href="${links.docs}">${c.docs}</a><a href="${links.security}">${c.security}</a><a href="${links.license}">${c.license}</a><a href="https://sbarah.com">${c.home}</a></p>
<p class="fine">${esc(c.trademark)}</p>
</div></footer>
<script id="page-data" type="application/json">${JSON.stringify(data).replaceAll('<','\\u003c')}</script>
</body>
</html>`;
}

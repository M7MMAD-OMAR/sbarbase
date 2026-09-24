// Interactions for the prerendered page. Everything is readable without this
// script; it only adds the walkthrough, the toggles and the motion. Motion is
// skipped when the reader prefers reduced motion.
const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>[...root.querySelectorAll(s)];
const data=JSON.parse($('#page-data')?.textContent||'{}');
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
const press=(buttons,active)=>buttons.forEach(b=>b.setAttribute('aria-pressed',String(b===active)));

// Day and night. An explicit choice is remembered for this reader only.
const root=document.documentElement;
try{const saved=localStorage.getItem('sbarbase-theme');if(saved==='light'||saved==='dark')root.dataset.theme=saved;}catch{}
$('#theme')?.addEventListener('click',()=>{
 const dark=root.dataset.theme?root.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
 root.dataset.theme=dark?'light':'dark';
 try{localStorage.setItem('sbarbase-theme',root.dataset.theme);}catch{}
});

// Sheets settle as they arrive; the current section is marked in the top bar.
const navLinks=new Map($$('.topbar nav a').map(a=>[a.getAttribute('href').slice(1),a]));
const seen=new IntersectionObserver(entries=>{for(const e of entries){
 if(e.isIntersecting&&!reduced&&!e.target.classList.contains('in')){e.target.classList.add('reveal','in');}
 const link=navLinks.get(e.target.id);if(link&&e.isIntersecting)navLinks.forEach(a=>a.classList.toggle('current',a===link));
}},{rootMargin:'-35% 0px -55% 0px'});
$$('main .sheet').forEach(s=>seen.observe(s));

// Today versus Sbarbase, and the environment slider.
const compare=$('.compare');
if(compare){
 const views=$$('.segmented button',compare);
 views.forEach(b=>b.addEventListener('click',()=>{compare.dataset.view=b.dataset.view;press(views,b);}));
 const range=$('#env-range'),out=$('#env-count'),r=data.rules;
 range?.addEventListener('input',()=>{
  const n=Number(range.value);compare.dataset.count=String(n);out.textContent=String(n);
  $('#n-containers').textContent=String(r.systemContainers+n*r.perEnvironmentContainers);
  $('#n-memory').textContent=String(r.systemMib+n*r.perEnvironmentMib);
 });
}

// The request walkthrough: twelve seconds, five steps, a packet along the wires.
const walk=$('.walk');
if(walk){
 const STEP=2400,TOTAL=STEP*5;
 const wires=['#w1','#w2','#w3'].map(id=>$(id,walk));
 const packet=$('.packet',walk),checks=$$('.check',walk),seek=$('#walk-seek'),play=$('#walk-play');
 const stepButtons=$$('.walk-steps button',walk);
 const gate=(()=>{const a=wires[0].getPointAtLength(wires[0].getTotalLength()),b=wires[1].getPointAtLength(0);return {x:(a.x+b.x)/2,y:(a.y+b.y)/2};})();
 const along=(wire,f)=>wire.getPointAtLength(wire.getTotalLength()*Math.min(1,Math.max(0,f)));
 const ease=f=>f<.5?2*f*f:1-Math.pow(-2*f+2,2)/2;
 let time=0,running=false,last=0,step=-1;
 function draw(t){
  const s=Math.min(4,Math.floor(t/STEP)),f=ease((t-s*STEP)/STEP);let p;
  if(s===0)p=along(wires[0],f);
  else if(s===1){const a=along(wires[0],1);p={x:a.x+(gate.x-a.x)*f,y:a.y+(gate.y-a.y)*f};}
  else if(s===2)p=along(wires[1],f);
  else if(s===3)p=along(wires[2],f);
  else{const k=f*3;p=k<1?along(wires[2],1-k):k<2?along(wires[1],2-k):along(wires[0],3-k);}
  packet.setAttribute('cx',p.x.toFixed(1));packet.setAttribute('cy',p.y.toFixed(1));
  checks.forEach((c,i)=>c.classList.toggle('on',t>=STEP+(i+.5)*STEP/4));
  if(s!==step){step=s;press(stepButtons,stepButtons[s]);walk.dataset.step=String(s);}
  seek.value=String(Math.round(t));
 }
 function frame(now){if(!running)return;time+=now-last;last=now;if(time>=TOTAL)time=0;draw(time);requestAnimationFrame(frame);}
 function setRunning(on){
  running=on&&!reduced;walk.classList.toggle('playing',running);play.setAttribute('aria-pressed',String(running));
  play.setAttribute('aria-label',running?data.pause:data.play);
  if(running){last=performance.now();requestAnimationFrame(frame);}
 }
 play.addEventListener('click',()=>setRunning(!running));
 $('#walk-replay').addEventListener('click',()=>{time=0;draw(0);setRunning(true);});
 seek.addEventListener('input',()=>{setRunning(false);walk.classList.add('stepped');time=Number(seek.value);draw(time);});
 stepButtons.forEach((b,i)=>b.addEventListener('click',()=>{setRunning(false);walk.classList.add('stepped');time=i*STEP+STEP*.999;draw(time);}));
 draw(0);
 if(reduced){setRunning(false);walk.classList.add('stepped');}
 else new IntersectionObserver(([e])=>{if(e.isIntersecting&&!walk.classList.contains('stepped'))setRunning(true);else if(!e.isIntersecting)setRunning(false);},{threshold:.35}).observe(walk);
 document.addEventListener('visibilitychange',()=>{if(document.hidden)setRunning(false);});
}

// Hierarchy: explain a level, and move an environment between servers (FLIP).
const hier=$('.hier');
if(hier){
 $$('[data-level]',hier).forEach(b=>b.addEventListener('click',()=>{
  const i=Number(b.dataset.level);hier.dataset.level=String(i);
  $('#level-name').textContent=data.levels[i][0];$('#level-text').textContent=data.levels[i][1];
 }));
 const move=$('#move'),chip=$('[data-env="a"]',hier),one=$('#server-1'),two=$('#server-2');
 move?.addEventListener('click',()=>{
  const first=chip.getBoundingClientRect();
  const away=chip.parentElement===one;(away?two:one).appendChild(chip);
  move.textContent=away?data.moveBack:data.moveAction;
  if(reduced)return;
  const last=chip.getBoundingClientRect();
  chip.animate([{transform:`translate(${first.left-last.left}px,${first.top-last.top}px) rotate(-6deg)`},{transform:'none'}],{duration:650,easing:'cubic-bezier(.3,1.3,.5,1)'});
 });
}

// Isolation: what each part shares, or keeps to itself.
const parts=$$('.part');
parts.forEach(b=>b.addEventListener('click',()=>{
 const p=data.parts[Number(b.dataset.part)];press(parts,b);
 $('#part-name').textContent=p[0];$('#part-text').textContent=p[2];
}));

// Recovery: four steps, the parcel follows.
const rec=$('.rec');
if(rec){
 const buttons=$$('[data-rec]',rec);
 buttons.forEach(b=>b.addEventListener('click',()=>{
  const i=Number(b.dataset.rec);rec.dataset.step=String(i);press(buttons,b);
  $('#rec-text').textContent=data.recSteps[i][1];
 }));
}

// Copy the install command.
const copy=$('#copy');
copy?.addEventListener('click',async()=>{
 const label=$('span',copy);
 try{await navigator.clipboard.writeText(copy.dataset.copy);label.textContent=data.copied;}
 catch{const range=document.createRange();range.selectNodeContents($('.terminal code'));const sel=getSelection();sel.removeAllRanges();sel.addRange(range);}
 setTimeout(()=>{label.textContent=data.copy;},2000);
});

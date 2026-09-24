// Interactions for the prerendered page. Everything is readable without this
// script; it adds the theme toggle, the request
// walkthrough and the copy buttons. The explainer starts only when the reader
// presses play, because it speaks. Motion is skipped under reduced motion.
const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>[...root.querySelectorAll(s)];
const data=JSON.parse($('#page-data')?.textContent||'{}');
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
const press=(buttons,active)=>buttons.forEach(b=>b.setAttribute('aria-pressed',String(b===active)));

// Day and night. An explicit choice is remembered for this reader only.
const root=document.documentElement;
const themeButton=$('#theme');
const isDark=()=>root.dataset.theme?root.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
try{const saved=localStorage.getItem('sbarbase-theme');if(saved==='light'||saved==='dark')root.dataset.theme=saved;}catch{}
const syncTheme=()=>themeButton?.setAttribute('aria-pressed',String(isDark()));
syncTheme();
themeButton?.addEventListener('click',()=>{
 root.dataset.theme=isDark()?'light':'dark';syncTheme();
 try{localStorage.setItem('sbarbase-theme',root.dataset.theme);}catch{}
});

// The current section is marked in the top bar.
const navLinks=new Map($$('.topbar nav a').map(a=>[a.getAttribute('href').slice(1),a]));
const seen=new IntersectionObserver(entries=>{for(const e of entries){
 const link=navLinks.get(e.target.id);if(link&&e.isIntersecting)navLinks.forEach(a=>a.classList.toggle('current',a===link));
}},{rootMargin:'-35% 0px -55% 0px'});
$$('main section[id]').forEach(s=>seen.observe(s));

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

// Copy an install step. Falls back to selecting that step's command.
$$('.copy').forEach(button=>button.addEventListener('click',async()=>{
 const label=$('span',button);
 try{await navigator.clipboard.writeText(button.dataset.copy);label.textContent=data.copied;}
 catch{const code=$('code',button.parentElement);const range=document.createRange();range.selectNodeContents(code);const sel=getSelection();sel.removeAllRanges();sel.addRange(range);}
 setTimeout(()=>{label.textContent=data.copy;},2000);
}));

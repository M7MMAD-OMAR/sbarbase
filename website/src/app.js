const data=JSON.parse(document.querySelector('#page-data').textContent);
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const player=document.querySelector('.request-player');
const play=document.querySelector('#play');
const progress=document.querySelector('#progress');
const packet=document.querySelector('.packet');
const routes=['M300 64V104','M300 158V182Q300 194 288 194H162Q150 194 150 207V229','M150 285V330Q150 342 163 342H300V358'];
const path=document.createElementNS('http://www.w3.org/2000/svg','path');
let playing=!reduced.matches,position=0,lastTime=0,visible=true,stage=-1;
function setPlayback(value){playing=value;play.setAttribute('aria-pressed',String(playing));play.setAttribute('aria-label',playing?data.pause:data.play);play.innerHTML=playing?'<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 5v14M16 5v14"/></svg>':'<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m8 5 11 7-11 7Z"/></svg>';player.classList.toggle('paused',!playing);document.body.classList.toggle('motion-paused',!playing);}
function paint(){const next=Math.min(3,Math.floor(position/3000));if(stage!==next){stage=next;player.dataset.stage=String(stage);document.querySelector('#request-description').textContent=data.stageNotes[stage];document.querySelector('.step-counter').textContent=`0${stage+1} / 04`;path.setAttribute('d',routes[Math.min(stage,2)]);}progress.value=String(position);document.querySelector('.play-time').textContent=`0:${String(Math.floor(position/1000)).padStart(2,'0')} / 0:12`;const point=path.getPointAtLength((position%3000)/3000*path.getTotalLength());packet.setAttribute('cx',String(point.x));packet.setAttribute('cy',String(point.y));packet.style.opacity=stage===3?'0':'.95';}
function tick(now){if(lastTime&&playing&&visible&&!document.hidden){position=(position+Math.min(now-lastTime,100))%12000;paint();}lastTime=now;requestAnimationFrame(tick);}
play.addEventListener('click',()=>setPlayback(!playing));
progress.addEventListener('input',()=>{setPlayback(false);position=Number(progress.value);paint();});
document.querySelector('.replay').addEventListener('click',()=>{position=0;paint();setPlayback(!reduced.matches);});
reduced.addEventListener('change',()=>{if(reduced.matches)setPlayback(false);});
new IntersectionObserver(([entry])=>{visible=entry.isIntersecting;},{threshold:.15}).observe(player);
setPlayback(playing);paint();requestAnimationFrame(tick);
const levelButtons=[...document.querySelectorAll('[data-level]')];
levelButtons.forEach(button=>button.addEventListener('click',()=>{const level=Number(button.dataset.level);levelButtons.forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});document.querySelector('#level-title').textContent=data.levels[level][1];document.querySelector('#level-description').textContent=data.levels[level][2];}));
const tabs=[...document.querySelectorAll('button[data-service]')];tabs.forEach(button=>button.addEventListener('click',()=>{const index=Number(button.dataset.service);tabs.forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});document.querySelector('.architecture-panel').dataset.service=String(index);document.querySelector('.service-explanation').textContent=data.serviceNotes[index];}));
const steps=[...document.querySelectorAll('[data-recovery]')];steps.forEach(button=>button.addEventListener('click',()=>{const index=Number(button.dataset.recovery);steps.forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});document.querySelector('.recovery-description').textContent=data.recoverStages[index][1];}));
document.querySelector('#compare').addEventListener('click',event=>{const button=event.currentTarget;const shared=button.getAttribute('aria-pressed')!=='true';button.setAttribute('aria-pressed',String(shared));document.querySelector('.comparison').dataset.shared=String(shared);document.querySelector('#compare-title').textContent=shared?data.compareAfter:data.compareBefore;});
// Deterministic playback frames for visual review, without changing normal behavior.
const seek=new URL(location.href).searchParams.get('t');if(seek!==null){setPlayback(false);position=Math.max(0,Math.min(11999,Number(seek)*1000||0));paint();}

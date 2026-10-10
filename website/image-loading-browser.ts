import {chromium} from '../lab/mfa-browser/node_modules/playwright/index.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';

const baseline=process.argv.includes('--baseline');
const artifacts=resolve(process.env.SBAR_SITE_IMAGE_ARTIFACTS??'output/playwright/website-images');
mkdirSync(artifacts,{recursive:true});
const root=resolve('website/dist');
const server=Bun.serve({hostname:'127.0.0.1',port:0,async fetch(request){
 const path=new URL(request.url).pathname;
 const file=resolve(root,'.'+(path.endsWith('/')?path+'index.html':path));
 if(!file.startsWith(root+'/'))return new Response(null,{status:404});
 const asset=Bun.file(file);return await asset.exists()?new Response(asset):new Response(null,{status:404});
}});
let browser;let total=0;const measurements:unknown[]=[];
function check(value:unknown,message:string){if(!value)throw new Error(message);total++;}
try{
 browser=await chromium.launch({headless:true});
 for(const lang of ['ar','en'])for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:900},deviceScaleFactor:1,colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage();const faults:string[]=[];
  page.on('pageerror',error=>faults.push(error.message));
  // Observe the poster rather than the first decoded movie frame.
  await page.route('**/*.mp4',route=>route.abort());
  await page.goto(`http://127.0.0.1:${server.port}/${lang==='en'?'en/':''}`);
  await page.locator('#explainer').waitFor();
  await page.waitForFunction(()=>performance.getEntriesByType('resource').some(entry=>entry.name.includes('explainer-poster')));
  await page.evaluate(()=>document.fonts.ready);
  const result=await page.evaluate(async()=>{
   const video=document.querySelector('video')!;
   const image=new Image();image.src=video.poster;await image.decode();
   const socialUrl=new URL(document.querySelector('meta[property="og:image"]')!.getAttribute('content')!);
   const localSocial=await fetch(socialUrl.pathname);const blob=await localSocial.blob();
   const social=new Image();const objectUrl=URL.createObjectURL(blob);social.src=objectUrl;await social.decode();URL.revokeObjectURL(objectUrl);
   return {poster:video.poster,width:image.naturalWidth,height:image.naturalHeight,
    posterEntries:performance.getEntriesByType('resource').filter(entry=>entry.name.includes('explainer-poster')).map(entry=>({url:entry.name,bytes:(entry as PerformanceResourceTiming).encodedBodySize})),
    social:{bytes:blob.size,type:blob.type,width:social.naturalWidth,height:social.naturalHeight},
    videoBounds:video.getBoundingClientRect().toJSON(),direction:document.documentElement.dir};
  });
  check(faults.length===0,'Website JavaScript error');
  check(result.width===1920&&result.height===1080,'Poster resolution changed');
  check(result.social.type==='image/jpeg'&&result.social.width===1200&&result.social.height===630,'Social JPEG is unavailable or changed dimensions');
  if(!baseline){
   check(result.poster.endsWith('.webp'),'Website did not select WebP poster');
   check(result.posterEntries.length===1&&result.posterEntries[0].bytes<60000,'Poster fetched twice or exceeds transfer budget');
   check(result.social.bytes<50000,'Social JPEG exceeds transfer budget');
  }
  measurements.push({lang,viewportWidth:width,viewportHeight:900,scale:1,...result});
  await page.screenshot({path:resolve(artifacts,`${baseline?'before':'after'}-${lang}-${width}.png`)});
  await context.close();
 }
 writeFileSync(resolve(artifacts,`${baseline?'before':'after'}-network.json`),JSON.stringify(measurements,null,2)+'\n');
 process.stdout.write(JSON.stringify({total,failed:0,skipped:0})+'\n');
}finally{await browser?.close();server.stop(true);}

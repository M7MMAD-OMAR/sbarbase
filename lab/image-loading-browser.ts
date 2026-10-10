import {chromium} from './mfa-browser/node_modules/playwright/index.mjs';
import {uiStatic} from './ui-static';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';

const baseline=process.argv.includes('--baseline');
const directory=resolve(process.env.SBAR_IMAGE_ARTIFACTS??'output/playwright/image-loading');
mkdirSync(directory,{recursive:true});
const server=Bun.serve({hostname:'127.0.0.1',port:0,async fetch(request){
 return await uiStatic(request)??new Response(null,{status:404});
}});
let browser;let total=0;
const measurements:unknown[]=[];
function check(condition:unknown,message:string){if(!condition)throw new Error(message);total++;}
try{
 browser=await chromium.launch({headless:true});
 for(const [width,height,scale] of [[1440,900,1],[1440,900,2],[900,900,1],[390,844,2]]){
  const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:scale,colorScheme:'dark'});
  const page=await context.newPage();const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.port}`);
  await page.getByRole('heading',{name:'Welcome back'}).waitFor();
  await page.waitForFunction(()=>Array.from(document.images).every(image=>image.complete));
  const result=await page.evaluate(()=>({
   source:(document.querySelector('.botanical-art img') as HTMLImageElement).currentSrc,
   resources:performance.getEntriesByType('resource').filter(entry=>entry.name.includes('cactus-photo')).map(entry=>({url:entry.name,bytes:(entry as PerformanceResourceTiming).encodedBodySize})),
   bounds:document.querySelector('.botanical-art')?.getBoundingClientRect().toJSON()
  }));
  measurements.push({width,height,scale,...result});
  check(errors.length===0,'Login JavaScript error');
  if(!baseline){
   if(width<=760)check(result.resources.length===0,'Hidden mobile artwork downloaded');
   else{
    check(result.resources.length===1,'Artwork fetched more than one variant');
    check(result.source.endsWith('.avif'),'Modern browser did not select AVIF');
    check(result.resources[0].bytes<(scale===2?150000:50000),'Artwork exceeds transfer budget');
   }
  }
  await page.screenshot({path:resolve(directory,`${baseline?'before':'after'}-${width}-${scale}.png`)});
  await context.close();
 }
 if(!baseline){
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  const page=await context.newPage();await page.goto(`http://127.0.0.1:${server.port}`);
  await page.getByRole('heading',{name:'Welcome back'}).waitFor();
  // Simulate a browser rejecting the preferred format through source media selection.
  await page.locator('source[type="image/avif"]').evaluate(node=>node.setAttribute('media','not all'));
  await page.waitForFunction(()=>(document.querySelector('.botanical-art img') as HTMLImageElement).currentSrc.endsWith('.webp'));
  await page.waitForFunction(()=>(document.querySelector('.botanical-art img') as HTMLImageElement).complete);
  check(await page.locator('.botanical-art img').evaluate(node=>(node as HTMLImageElement).naturalWidth>0),'WebP fallback failed to decode');
  await context.close();
 }
 writeFileSync(resolve(directory,`${baseline?'before':'after'}-network.json`),JSON.stringify(measurements,null,2)+'\n');
 process.stdout.write(JSON.stringify({total,failed:0,skipped:0})+'\n');
}finally{await browser?.close();server.stop(true);}

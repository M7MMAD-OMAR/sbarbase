import {expect,test} from 'bun:test';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {build,createLogger} from 'vite';
import productionConfig from '../vite.config.mts';

test('production client boundaries preserve directives, tree shaking and unrelated diagnostics',async()=>{
 const fixture=await mkdtemp(join(tmpdir(),'sbarbase-client-boundaries-'));
 const warnings:string[]=[];
 const logger=createLogger('warn');
 logger.warn=message=>{warnings.push(message);};
 logger.warnOnce=message=>{warnings.push(message);};
 try {
  await writeFile(join(fixture,'client.js'),`/* Upstream dependency banner. */
"use strict";
"use client";
globalThis.__clientBoundaryExecuted=true;
export function value(){return globalThis.__clientBoundaryInput??42;}
export const unused="UNUSED_CLIENT_EXPORT_SENTINEL";
`);
  await writeFile(join(fixture,'other.js'),'"custom boundary";export const other=1;');
  await writeFile(join(fixture,'main.js'),`import {value} from './client.js';
import {other} from './other.js';
globalThis.__clientBoundaryResult=value()+other;
`);
  const result=await build({
   configFile:false,root:fixture,plugins:productionConfig.plugins,customLogger:logger,
   build:{write:false,rolldownOptions:{input:join(fixture,'main.js')}}
  });
  const outputs=Array.isArray(result)?result:[result];
  const chunks=outputs.flatMap(output=>{
   if(!('output' in output))throw new Error('Expected completed build output.');
   return output.output.filter(asset=>asset.type==='chunk');
  });
  expect(chunks).toHaveLength(1);
  const code=chunks[0]?.code;
  if(!code)throw new Error('Expected an emitted JavaScript chunk.');
  expect(code).toMatch(/^["']use client["'];/);
  expect(code).not.toContain('UNUSED_CLIENT_EXPORT_SENTINEL');
  expect(warnings.some(message=>message.includes('custom boundary'))).toBe(true);
  expect(warnings.some(message=>message.includes('directive "use client"'))).toBe(false);

  // Execute the actual bundle to check that moving its directive keeps behavior.
  const runtime=globalThis as typeof globalThis&{
   __clientBoundaryInput?:number;
   __clientBoundaryExecuted?:boolean;
   __clientBoundaryResult?:number;
  };
  try {
   runtime.__clientBoundaryInput=100;
   await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
   expect(runtime.__clientBoundaryExecuted).toBe(true);
   expect(runtime.__clientBoundaryResult).toBe(101);
  } finally {
   delete runtime.__clientBoundaryInput;
   delete runtime.__clientBoundaryExecuted;
   delete runtime.__clientBoundaryResult;
  }
 } finally {
  await rm(fixture,{recursive:true,force:true});
 }
});

import {readFileSync,statSync} from 'node:fs';
import {join} from 'node:path';

/** The steps of a Studio start, in order (lab/studio.py STEPS). The console names each one. */
export const STUDIO_STEPS=['checks','database','network','studio','meta','serving','warm','route'] as const;
export type StudioStep=typeof STUDIO_STEPS[number];
export type StudioProgress={step:StudioStep;startedAt:number;steps:StudioStep[]};
export const STUDIO_PROGRESS_DIRECTORY=join('.lab','upstream','studio-progress');

/** Which step of a start the worker reports for one environment, or null when it reports none. The file is the
 * worker's own and small; anything that does not match exactly is ignored, never trusted. */
export function studioProgress(runtime:string,directory=STUDIO_PROGRESS_DIRECTORY):StudioProgress|null {
  if(!/^e_[a-f0-9]{24}$/.test(runtime))return null;
  try {
    const path=join(directory,runtime+'.json');
    if(statSync(path).size>4096)return null;
    const value=JSON.parse(readFileSync(path,'utf8')) as {started?:unknown;step?:unknown;steps?:unknown};
    const known=(id:unknown):id is StudioStep=>typeof id==='string'&&(STUDIO_STEPS as readonly string[]).includes(id);
    if(!known(value.step)||typeof value.started!=='number'||!Number.isFinite(value.started)||!Array.isArray(value.steps))return null;
    const done=STUDIO_STEPS.filter(id=>(value.steps as unknown[]).some(item=>!!item&&typeof item==='object'&&(item as {id?:unknown}).id===id));
    return {step:value.step,startedAt:value.started,steps:done};
  } catch {return null;}
}

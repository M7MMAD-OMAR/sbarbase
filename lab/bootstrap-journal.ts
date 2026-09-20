import {readFileSync,openSync,writeFileSync,fsyncSync,closeSync,renameSync} from 'node:fs';
import {dirname} from 'node:path';
import type {BootstrapJournal,BootstrapState} from '../src/control/bootstrap';

export function bootstrapJournal(path:string):BootstrapJournal {
 return {
  read(){try{return JSON.parse(readFileSync(path,'utf8'));}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return null;throw error;}},
  write(state:BootstrapState) {
   const temporary=path+'.pending',fd=openSync(temporary,'w',0o600);
   try{writeFileSync(fd,JSON.stringify(state));fsyncSync(fd);}finally{closeSync(fd);}
   renameSync(temporary,path);
   const directory=openSync(dirname(path),'r');try{fsyncSync(directory);}finally{closeSync(directory);}
  }
 };
}

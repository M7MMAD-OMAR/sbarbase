import {test,expect} from 'bun:test';
import {runInNewContext} from 'node:vm';
import {studioWorkspaceJs} from '../src/control/studio-workspace';

// Execute the shipped browser asset, including its actual click handler.
async function switchFrom(origin:string,href:string){
 const elements=new Map<string,any>();
 for(const id of ['organization','project','environment','open','refresh','status','state','console'])elements.set(id,{
  value:'',options:[],textContent:'',disabled:false,handlers:{} as Record<string,Function>,
  replaceChildren(){this.options=[];},append(value:unknown){this.options.push(value);},
  addEventListener(name:string,handler:Function){this.handlers[name]=handler;},
 });
 let assigned='';
 const navigation={selection:{organization:'org',project:'project'},organizations:[{id:'org',name:'Org'}],projects:[{id:'project',name:'Project'}],
  environments:[{id:'target',name:'Stage',ready:true,state:'running'}],current:{organization:{id:'org',name:'Org'},project:{id:'project',name:'Project'},environment:{id:'source',name:'Production'}},consoleUrl:origin};
 runInNewContext(studioWorkspaceJs,{
  document:{getElementById:(id:string)=>elements.get(id),createElement:()=>({value:'',textContent:''}),title:''},
  location:{...new URL(origin),protocol:new URL(origin).protocol,port:new URL(origin).port,href:origin,assign:(value:string)=>{assigned=value;}},
  URL,URLSearchParams,AbortController,
  fetch:async(path:string)=>({ok:true,status:200,json:async()=>path.startsWith('/__sbarbase/navigation')?navigation:{href}}),
 });
 await new Promise(resolve=>setTimeout(resolve,0));
 await elements.get('open').handlers.click();
 return {assigned,error:elements.get('status').textContent};
}

test('shipped Studio workspace accepts authenticated public and local switch destinations',async()=>{
 for(const [origin,href] of [
  ['https://studio-aaaaaaaaaaaaaaaaaaaaaaaa.example.com','https://studio-bbbbbbbbbbbbbbbbbbbbbbbb.example.com/__sbarbase/enter?ticket=signed'],
  ['http://aaaaaaaaaaaaaaaaaaaaaaaa.studio.localhost:8787','http://bbbbbbbbbbbbbbbbbbbbbbbb.studio.localhost:8787/__sbarbase/enter?ticket=signed'],
 ])expect((await switchFrom(origin!,href!)).assigned).toBe(href!);
});

test('shipped Studio workspace refuses altered transport, credentials and non-entry destinations',async()=>{
 for(const href of ['http://studio-b.example.com/__sbarbase/enter?ticket=signed','https://studio-b.example.com:444/__sbarbase/enter?ticket=signed',
  'https://user:password@studio-b.example.com/__sbarbase/enter?ticket=signed','https://studio-b.example.com/__sbarbase/workspace?ticket=signed',
  'https://studio-b.example.com/__sbarbase/enter']){
  const result=await switchFrom('https://studio-a.example.com',href);expect(result.assigned).toBe('');expect(result.error).toBe('Invalid Studio destination.');
 }
});

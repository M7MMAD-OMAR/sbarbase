import {openUpstreamApplication} from './upstream-app';
import {readFileSync,unlinkSync} from 'node:fs';

// Local experimental API only. No remote bind or default production exposure.
const app=openUpstreamApplication();
const server=Bun.serve({hostname:'127.0.0.1',port:0,fetch:app.handler});
await Bun.write('.lab/upstream/server.json',JSON.stringify({url:`http://127.0.0.1:${server.port}`,pid:process.pid}));
console.log(`Local Sbarbase API: http://127.0.0.1:${server.port}`);
function stop(){
 server.stop(true);app.close();
 try {if(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')).pid===process.pid)unlinkSync('.lab/upstream/server.json');}catch {}
 process.exit(0);
}
process.on('SIGINT',stop);process.on('SIGTERM',stop);

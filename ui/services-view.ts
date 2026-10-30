/** The words of the Services page, pure so they are tested. The numbers come from GET /environments/{id}/services (src/control/services.ts). */
export type ServiceId='realtime'|'functions'|'database'|'cron'|'studio';
export type ServiceState='off'|'starting'|'stopping'|'on'|'failed'|'sleeping'|'waking';
export type Mode='off'|'on'|'on_demand';
export type Service={id:ServiceId;state:ServiceState;failure:string|null;updatedAt:number|null;memoryBytes:number|null;memoryLimitBytes:number|null;
 costBytes:number|null;lastUsedAt:number|null;tracked:boolean;inUse:number|null;unused?:{days:number;neverUsed:boolean}|null;
 mode?:Mode;idleMinutes?:number;onDemandAvailable?:boolean};
export type Services={runtime:string;services:Service[];canChange:boolean;killSwitch?:boolean;limits?:{wakesPerHour:number;holdSeconds:number;idleFloorMinutes:number};stats:{capturedAt:number|null;stale:boolean};
 capacity:{availableBytes:number|null;totalBytes:number|null}};

/** The route of each service's own switch, under /environments/{id}. Direct database access and Studio have their own pages. */
export const SWITCH_ROUTE:Partial<Record<ServiceId,string>>={realtime:'/realtime',functions:'/functions',cron:'/scheduled-jobs'};

export const NAMES:Record<ServiceId,string>={realtime:'Realtime',functions:'Edge Functions',database:'Direct database access',cron:'Scheduled jobs',studio:'Studio'};

/** What each service is, in one sentence of plain words. */
export const ABOUT:Record<ServiceId,string>={
 realtime:'Listen to database changes, and send broadcast and presence messages between clients, with supabase.channel(). Every table in the public schema is published; row level security decides who receives a change.',
 functions:'Server-side TypeScript functions that run next to your database. Deploying a function turns the runtime on only when it is Off.',
 cron:'Run SQL on a schedule with cron.schedule(), and call other services from the database with net.http_post(). Jobs run as the environment developer role.',
 database:'A login and a connection string for tools and servers outside Sbarbase. It runs no container of its own.',
 studio:'Supabase Studio for this environment. It starts when you open it and stops when you stop using it.',
};

/** What turning a service on adds, when it adds a container. */
export const COST_NOTE:Record<ServiceId,string>={
 realtime:'Runs as its own small service for this environment, and only while it runs.',
 functions:'Runs as its own service for this environment, and only while it runs. Your deployed code and secrets are kept when it is not running.',
 cron:'Turning it off stops the runs and keeps your jobs and their history. Requests from the database cannot reach private addresses or this server, except through its public address.',
 database:'Turning it off closes the login and keeps your data.',
 studio:'Started and stopped from the Overview page.',
};

const GIB=1024**3,MIB=1024**2;
/** 183 MiB, 2.1 GiB. Whole MiB under a GiB, one decimal above. */
export function memory(value:number):string {
 if(value>=GIB)return `${Math.round(value/GIB*10)/10} GiB`;
 return `${Math.round(value/MIB)} MiB`;
}

export const STATE_LABEL:Record<ServiceState,string>={off:'Off',starting:'Starting…',stopping:'Stopping…',on:'On',failed:'Not applied',sleeping:'Sleeping',waking:'Waking…'};
export const STATE_CLASS:Record<ServiceState,string>={off:'',starting:'pending',stopping:'pending',on:'applied',failed:'failed',sleeping:'',waking:'pending'};

/** "183 MiB of 320 MiB" while it runs and a fresh sample exists; nothing to show otherwise. */
export function memoryText(service:Service):string {
 if(service.id==='database'||service.id==='cron')return 'No container of its own';
 if(service.memoryBytes===null)return service.state==='on'?'Not measured yet':service.state==='sleeping'||service.state==='waking'?'None while asleep':'None while off';
 return service.memoryLimitBytes===null?memory(service.memoryBytes):`${memory(service.memoryBytes)} of ${memory(service.memoryLimitBytes)}`;
}

/** The sentence under a switch that is off: what turning the service on needs, against what the server has free. */
/** Every state a service can be in, said once in plain words for the line under a service and for a screen reader. */
export const STATE_NOTE:Partial<Record<ServiceState,string>>={sleeping:'Asleep: no container is running. The first request starts it.',waking:'Starting. A request asked for it, or the operator has turned On demand off for now.'};

export function costText(service:Service,capacity:Services['capacity']):string|null {
 if(service.costBytes===null)return null;
 const free=capacity.availableBytes;
 return free===null?`Turning it on needs about ${memory(service.costBytes)}.`:`Turning it on needs about ${memory(service.costBytes)}. This server has about ${memory(free)} of memory available now and keeps a reserve, so it can still say no.`;
}

/** Time since last use, in the words the page uses. `tracked` false means the product does not record it for this service. */
export function lastUsedText(service:Service,now:number,ago:(then:number,now:number)=>string):string {
 if(!service.tracked)return 'Not recorded';
 return service.lastUsedAt===null?'Never used':ago(service.lastUsedAt,now);
}

/** The one line under a service saved as On demand. Written for someone who has never read the guide, so a slow first request never looks like a bug. */
export function onDemandLine(minutes:number):string {
 return `Starts on the first request, stops after ${minutes} quiet ${minutes===1?'minute':'minutes'}. The first request after a pause can take a few seconds.`;
}

/** What choosing a mode does, in words, before it is applied. `from` is the mode saved now, `running` whether the container runs now. */
export function modeEffect(service:Service,draft:Mode,minutes:number,from:Mode,running:boolean):string {
 const name=NAMES[service.id],kept=service.id==='functions'?' Your deployed code and secrets are kept.':'';
 if(draft==='off')return `${name} stops${running?' now':''} and nothing starts it again. Requests to it fail until you turn it on.${kept}`;
 if(draft==='on')return running&&from==='on'?`${name} keeps running all the time and holds its memory whether it is used or not.`:
  `${name} starts now and runs all the time, holding its memory whether it is used or not.`;
 const base=onDemandLine(minutes);
 if(from==='on'&&running)return `${base} It keeps running now, and stops by itself once it has been quiet that long with nothing open.`;
 if(from==='off')return `${base} Nothing starts now: it stays asleep until something uses it.`;
 return base;
}

/** The limits the console shows and cannot change, in one sentence. */
export function limitsText(limits:NonNullable<Services['limits']>):string {
 return `At most ${limits.wakesPerHour} starts an hour for each service, one start at a time on the whole server. A first request waits up to ${limits.holdSeconds} seconds for the start, then gets a retry answer. Open connections, running calls and every request that reaches a service keep it awake for its quiet period.`;
}

/** What the page asks before it turns off a service that is in use, or null when nothing is. Open connections would close. */
export function confirmOff(service:Service):string|null {
 const count=service.inUse??0;
 if(service.state!=='on'||count<1)return null;
 if(service.id==='realtime')return `${count} ${count===1?'connection is':'connections are'} open right now. Turning Realtime off closes ${count===1?'it':'them'}; clients reconnect once it is on again.`;
 if(service.id==='functions')return `${count} ${count===1?'call is':'calls are'} running right now. Turning Edge Functions off waits up to 10 seconds for ${count===1?'it':'them'} to finish, and new calls are asked to retry meanwhile. Your deployed code and secrets are kept.`;
 return null;
}

/** The service is changing: the page asks again in a few seconds. */
export const changing=(service:Service)=>service.state==='starting'||service.state==='stopping'||service.state==='waking';

/** The memory figures are older than the sampler's cadence allows. */
export function staleText(stats:Services['stats'],now:number,ago:(then:number,now:number)=>string):string|null {
 if(!stats.stale)return null;
 return stats.capturedAt===null?'Memory figures are not available yet. The server measures them once a minute.':`Memory figures are out of date (measured ${ago(stats.capturedAt,now)}).`;
}

/** The hint under a service that is on and sits unused: how long, what it holds, and the question. Never an action by itself. */
export function unusedText(service:Service):string|null {
 const hint=service.unused;
 if(!hint)return null;
 const days=`${hint.days} ${hint.days===1?'day':'days'}`;
 const what=service.id==='realtime'?'connection':'call';
 const held=service.memoryBytes===null?'':`, ${memory(service.memoryBytes)}`;
 return hint.neverUsed?`No ${what} since it was turned on ${days} ago${held}. Turn it off?`:`No ${what} for ${days}${held}. Turn it off?`;
}

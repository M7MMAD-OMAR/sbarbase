import {Database} from 'bun:sqlite';
import {validatePlacement,type RuntimePlacement,type RuntimeRouting} from './placement';
import {randomUUID,randomBytes} from 'node:crypto';
import {chmodSync} from 'node:fs';

export type MembershipRole = 'owner' | 'admin' | 'viewer';
type Project = {id:string;organization:string;name:string};
type Environment = {id:string;project:string;name:string};
export type ProvisionFailure = 'capacity_exceeded' | 'runtime_failed';
export type ProvisionJob = {environment:string;runtime:string;actor:string;organization:string;state:string;attempt:number;claim:string|null;failure:ProvisionFailure|null};

/** Internal control-plane boundary. Actor IDs must come from verified management
 * authentication, never request bodies or application JWTs. Not an HTTP API.
 * Placement and runtime credentials deliberately do not belong to ownership.
 */
export class Catalog {
  private db:Database;
  constructor(path:string) {
    this.db=new Database(path,{create:true,strict:true});
    if(path!==':memory:') chmodSync(path,0o600);
    this.db.exec(`PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA journal_mode=DELETE;
      CREATE TABLE IF NOT EXISTS organizations(id TEXT PRIMARY KEY,name TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS installation_bootstrap(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), operation TEXT NOT NULL UNIQUE,
        actor TEXT NOT NULL, organization TEXT NOT NULL REFERENCES organizations(id));
      CREATE TABLE IF NOT EXISTS memberships(
        organization TEXT NOT NULL REFERENCES organizations(id), actor TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('owner','admin','viewer')),
        PRIMARY KEY(organization,actor));
      CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,
        organization TEXT NOT NULL REFERENCES organizations(id),name TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS environments(id TEXT PRIMARY KEY,
        project TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,
        UNIQUE(project,name));
      CREATE TABLE IF NOT EXISTS provision_jobs(
        environment TEXT PRIMARY KEY REFERENCES environments(id),runtime TEXT NOT NULL UNIQUE,
        actor TEXT NOT NULL,organization TEXT NOT NULL REFERENCES organizations(id),
        state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
        attempt INTEGER NOT NULL DEFAULT 0,claim TEXT);
      CREATE TABLE IF NOT EXISTS provision_effect_results(
        environment TEXT NOT NULL REFERENCES provision_jobs(environment), attempt INTEGER NOT NULL,
        runtime TEXT NOT NULL, claim TEXT NOT NULL, exit_code INTEGER NOT NULL CHECK(exit_code IN (0,75)),
        PRIMARY KEY(environment,attempt));
      CREATE TABLE IF NOT EXISTS provision_recovery_decisions(
        environment TEXT NOT NULL REFERENCES provision_jobs(environment),attempt INTEGER NOT NULL,
        runtime TEXT NOT NULL,claim TEXT NOT NULL,receipt_token TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('retry','failed')),
        PRIMARY KEY(environment,attempt));
      CREATE TABLE IF NOT EXISTS runtime_routing(
        runtime TEXT PRIMARY KEY REFERENCES provision_jobs(runtime),
        revision INTEGER NOT NULL CHECK(revision>0),
        maintenance INTEGER NOT NULL CHECK(maintenance IN (0,1)),placement TEXT);
      CREATE TABLE IF NOT EXISTS audit_events(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
        action TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL, at INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS projects_organization ON projects(organization);
      CREATE INDEX IF NOT EXISTS environments_project ON environments(project);`);
    this.db.transaction(()=>{
      const columns=this.db.query<{name:string},[]>('PRAGMA table_info(provision_jobs)').all();
      if(!columns.some(column=>column.name==='failure'))
        this.db.exec("ALTER TABLE provision_jobs ADD COLUMN failure TEXT CHECK(failure IS NULL OR failure IN ('capacity_exceeded','runtime_failed'))");
    }).immediate();
  }
  private name(value:string) {
    if(typeof value!=='string'||!value.trim()||value.length>100||/[\x00-\x1f]/.test(value))
      throw new Error('Invalid name');
    return value.trim();
  }
  private actor(value:string) {
    if(typeof value!=='string'||!value||value.length>200||/[\x00-\x20]/.test(value))
      throw new Error('Invalid actor');
    return value;
  }
  private require(actor:string,organization:string,roles:MembershipRole[]) {
    this.actor(actor);
    const membership=this.db.query<{role:MembershipRole},[string,string]>(
      'SELECT role FROM memberships WHERE organization=? AND actor=?').get(organization,actor);
    if(!membership||!roles.includes(membership.role)) throw new Error('Forbidden');
  }
  private project(actor:string,id:string,roles:MembershipRole[]):Project {
    const project=this.db.query<Project,[string]>('SELECT * FROM projects WHERE id=?').get(id);
    if(!project) throw new Error('Forbidden');
    this.require(actor,project.organization,roles);
    return project;
  }
  private record(actor:string,action:string,subject:string,detail:object) {
    this.db.query('INSERT INTO audit_events(actor,action,subject,detail,at) VALUES (?,?,?,?,?)')
      .run(actor,action,subject,JSON.stringify(detail),Date.now());
  }
  /** Trusted operator entry point, never an unauthenticated HTTP endpoint. */
  createOrganization(owner:string,name:string):string {
    this.actor(owner); const title=this.name(name),id=randomUUID();
    return this.db.transaction(()=>{
      this.db.query('INSERT INTO organizations VALUES (?,?)').run(id,title);
      this.db.query('INSERT INTO memberships VALUES (?,?,?)').run(id,owner,'owner');
      this.record(owner,'organization.created',id,{});return id;
    }).immediate();
  }
  installationBootstrap():{operation:string;actor:string;organization:string}|null {
    return this.db.query<{operation:string;actor:string;organization:string},[]>(
      'SELECT operation,actor,organization FROM installation_bootstrap WHERE singleton=1').get();
  }
  initializeInstallation(operation:string,owner:string,name:string):string {
    this.actor(owner);this.actor(operation);const title=this.name(name);
    return this.db.transaction(()=>{
      const existing=this.installationBootstrap();
      if(existing) {
        if(existing.operation!==operation||existing.actor!==owner)throw new Error('Installation already initialized');
        // Retry must not resurrect revoked authority.
        this.require(owner,existing.organization,['owner']);
        return existing.organization;
      }
      const id=randomUUID();
      this.db.query('INSERT INTO organizations VALUES (?,?)').run(id,title);
      this.db.query('INSERT INTO memberships VALUES (?,?,?)').run(id,owner,'owner');
      this.db.query('INSERT INTO installation_bootstrap VALUES (1,?,?,?)').run(operation,owner,id);
      this.record(owner,'installation.initialized',id,{});return id;
    }).immediate();
  }
  listOrganizations(actor:string):{id:string;name:string;role:MembershipRole}[] {
    this.actor(actor);
    return this.db.query<{id:string;name:string;role:MembershipRole},[string]>(
      'SELECT o.id,o.name,m.role FROM organizations o JOIN memberships m ON m.organization=o.id WHERE m.actor=? ORDER BY o.id').all(actor);
  }
  setMember(actor:string,organization:string,target:string,role:MembershipRole|null) {
    this.actor(target);
    if(role!==null&&!['owner','admin','viewer'].includes(role)) throw new Error('Invalid role');
    this.db.transaction(()=>{
      this.require(actor,organization,['owner']);
      const previous=this.db.query<{role:MembershipRole},[string,string]>(
        'SELECT role FROM memberships WHERE organization=? AND actor=?').get(organization,target);
      if(previous?.role==='owner'&&role!=='owner') {
        const count=this.db.query<{n:number},[string]>(
          "SELECT count(*) n FROM memberships WHERE organization=? AND role='owner'").get(organization);
        if(!count||count.n<=1) throw new Error('Last owner cannot be removed');
      }
      if(role===null) this.db.query('DELETE FROM memberships WHERE organization=? AND actor=?').run(organization,target);
      else this.db.query(`INSERT INTO memberships VALUES (?,?,?)
        ON CONFLICT(organization,actor) DO UPDATE SET role=excluded.role`).run(organization,target,role);
      this.record(actor,'membership.changed',organization,{target,role});
    }).immediate();
  }
  createProject(actor:string,organization:string,name:string):string {
    const title=this.name(name),id=randomUUID();
    return this.db.transaction(()=>{
      this.require(actor,organization,['owner','admin']);
      this.db.query('INSERT INTO projects VALUES (?,?,?)').run(id,organization,title);
      this.record(actor,'project.created',id,{organization});return id;
    }).immediate();
  }
  createEnvironment(actor:string,project:string,name:string):string {
    const title=this.name(name),id=randomUUID();
    return this.db.transaction(()=>{
      const parent=this.project(actor,project,['owner','admin']);
      this.db.query('INSERT INTO environments VALUES (?,?,?)').run(id,project,title);
      this.db.query('INSERT INTO provision_jobs(environment,runtime,actor,organization,state) VALUES (?,?,?,?,?)')
        .run(id,'e_'+randomBytes(12).toString('hex'),actor,parent.organization,'queued');
      this.record(actor,'environment.created',id,{project});return id;
    }).immediate();
  }
  listProjects(actor:string,organization:string):Project[] {
    return this.db.transaction(()=>{
      this.require(actor,organization,['owner','admin','viewer']);
      return this.db.query<Project,[string]>('SELECT * FROM projects WHERE organization=? ORDER BY id').all(organization);
    })();
  }
  listEnvironments(actor:string,project:string):Environment[] {
    return this.db.transaction(()=>{
      this.project(actor,project,['owner','admin','viewer']);
      return this.db.query<Environment,[string]>('SELECT * FROM environments WHERE project=? ORDER BY id').all(project);
    })();
  }
  withReadyEnvironment<T>(actor:string,environment:string,write:boolean,operation:(job:ProvisionJob)=>T):T {
    return this.db.transaction(()=>{
      const env=this.db.query<Environment,[string]>('SELECT * FROM environments WHERE id=?').get(environment);
      if(!env) throw new Error('Forbidden');
      this.project(actor,env.project,write?['owner','admin']:['owner','admin','viewer']);
      const job=this.db.query<ProvisionJob,[string]>('SELECT * FROM provision_jobs WHERE environment=?').get(environment);
      if(!job||job.state!=='succeeded') throw new Error('Environment is not ready');
      return operation(job);
    }).immediate();
  }
  runtimeReady(runtime:string):boolean {
    return !!this.db.query<{environment:string},[string]>(
      "SELECT environment FROM provision_jobs WHERE runtime=? AND state='succeeded'").get(runtime);
  }
  getProvision(actor:string,environment:string):ProvisionJob {
    const env=this.db.query<Environment,[string]>('SELECT * FROM environments WHERE id=?').get(environment);
    if(!env) throw new Error('Forbidden');
    this.project(actor,env.project,['owner','admin','viewer']);
    const job=this.db.query<ProvisionJob,[string]>('SELECT * FROM provision_jobs WHERE environment=?').get(environment);
    if(!job) throw new Error('No provisioning operation');
    return job;
  }
  /** Worker-only methods. Caller must hold the installation's exclusive worker
   * lock across recovery, claim, external effects and completion. No time-based
   * lease stealing: a slow Docker operation must not overlap another worker.
   */
  recoverProvisioning() {
    this.db.query("UPDATE provision_jobs SET state='queued',claim=NULL WHERE state='running'").run();
  }
  claimProvision():ProvisionJob|null {
    return this.db.transaction(()=>{
      const jobs=this.db.query<ProvisionJob,[]>("SELECT * FROM provision_jobs WHERE state='queued' ORDER BY environment").all();
      for(const job of jobs) {
        const env=this.db.query<Environment,[string]>('SELECT * FROM environments WHERE id=?').get(job.environment);
        try {
          if(!env) throw new Error('Forbidden');
          const parent=this.project(job.actor,env.project,['owner','admin']);
          if(parent.organization!==job.organization) throw new Error('Forbidden');
        } catch {
          this.db.query("UPDATE provision_jobs SET state='cancelled' WHERE environment=?").run(job.environment);
          this.record('system','provision.cancelled',job.environment,{});continue;
        }
        const claim=randomUUID();
        this.db.query("UPDATE provision_jobs SET state='running',attempt=attempt+1,claim=? WHERE environment=?")
          .run(claim,job.environment);
        this.record('system','provision.started',job.environment,{attempt:job.attempt+1});
        return {...job,state:'running',attempt:job.attempt+1,claim};
      }
      return null;
    }).immediate();
  }
  finishProvision(environment:string,claim:string,success:boolean,failure:ProvisionFailure='runtime_failed') {
    if(!['capacity_exceeded','runtime_failed'].includes(failure)) throw new Error('Invalid provisioning failure code');
    return this.db.transaction(()=>{
      const result=this.db.query("UPDATE provision_jobs SET state=?,failure=?,claim=NULL WHERE environment=? AND claim=? AND state='running'")
        .run(success?'succeeded':'failed',success?null:failure,environment,claim);
      if(result.changes!==1) throw new Error('Stale provisioning claim');
      this.record('system',success?'provision.succeeded':'provision.failed',environment,success?{}:{failure});
    }).immediate();
  }
  /** Worker-only durable outcome settlement. Receipt consumption happens after this commit. */
  applyProvisionReceipt(environment:string,runtime:string,claim:string,attempt:number,exitCode:number) {
    if(![0,75].includes(exitCode))throw new Error('Unresolved provisioning outcome');
    this.db.transaction(()=>{
      const job=this.db.query<ProvisionJob,[string]>('SELECT * FROM provision_jobs WHERE environment=?').get(environment);
      if(!job||job.runtime!==runtime||job.attempt<attempt)throw new Error('Provisioning receipt mismatch');
      const success=exitCode===0;
      const prior=this.db.query<{runtime:string;claim:string;exit_code:number},[string,number]>(
        'SELECT runtime,claim,exit_code FROM provision_effect_results WHERE environment=? AND attempt=?').get(environment,attempt);
      if(prior) {
        if(prior.runtime!==runtime||prior.claim!==claim||prior.exit_code!==exitCode)throw new Error('Provisioning receipt mismatch');
        return;
      }
      if(job.attempt!==attempt||job.state!=='running'||job.claim!==claim)throw new Error('Provisioning receipt mismatch');
      this.finishProvision(environment,claim,success,'capacity_exceeded');
      this.db.query('INSERT INTO provision_effect_results(environment,attempt,runtime,claim,exit_code) VALUES (?,?,?,?,?)')
        .run(environment,attempt,runtime,claim,exitCode);
    }).immediate();
  }
  /** Fresh worker/effect/operation ownership and preflight proof are required by the caller. */
  recoverPreflightReceipt(environment:string,runtime:string,claim:string,attempt:number,token:string):'requeued'|'failed' {
    return this.db.transaction(()=>{
      const job=this.db.query<ProvisionJob,[string]>('SELECT * FROM provision_jobs WHERE environment=?').get(environment);
      if(!job||job.runtime!==runtime||job.attempt<attempt)throw new Error('Preflight receipt mismatch');
      const prior=this.db.query<{runtime:string;claim:string;receipt_token:string;decision:string},[string,number]>(
        'SELECT runtime,claim,receipt_token,decision FROM provision_recovery_decisions WHERE environment=? AND attempt=?').get(environment,attempt);
      if(prior) {
        if(prior.runtime!==runtime||prior.claim!==claim||prior.receipt_token!==token)throw new Error('Preflight receipt mismatch');
        return prior.decision==='retry'?'requeued':'failed';
      }
      if(job.state!=='running'||job.attempt!==attempt||job.claim!==claim)throw new Error('Preflight receipt mismatch');
      const retries=this.db.query<{n:number},[string]>(
        "SELECT count(*) n FROM provision_recovery_decisions WHERE environment=? AND decision='retry'").get(environment)!.n;
      const retry=retries<2;
      this.db.query('UPDATE provision_jobs SET state=?,claim=NULL,failure=? WHERE environment=?')
        .run(retry?'queued':'failed',retry?null:'runtime_failed',environment);
      this.db.query('INSERT INTO provision_recovery_decisions VALUES (?,?,?,?,?,?)')
        .run(environment,attempt,runtime,claim,token,retry?'retry':'failed');
      this.record('system',retry?'provision.preflight_requeued':'provision.preflight_retry_limit',environment,{attempt});
      return retry?'requeued':'failed';
    }).immediate();
  }
  retryProvision(actor:string,environment:string) {
    this.db.transaction(()=>{
      const env=this.db.query<Environment,[string]>('SELECT * FROM environments WHERE id=?').get(environment);
      if(!env) throw new Error('Forbidden');
      const parent=this.project(actor,env.project,['owner','admin']);
      const result=this.db.query("UPDATE provision_jobs SET state='queued',actor=?,organization=?,claim=NULL,failure=NULL WHERE environment=? AND state IN ('failed','cancelled')")
        .run(actor,parent.organization,environment);
      if(result.changes!==1) throw new Error('Operation is not retryable');
      this.record(actor,'provision.retried',environment,{});
    }).immediate();
  }
  /** Metadata-only transfer. Requires owner authority in both organizations.
   * Runtime transfer must additionally revoke/rotate previously exposed access.
   * Do not expose as a complete project transfer until that workflow exists.
   */
  transferProject(actor:string,project:string,destination:string) {
    this.db.transaction(()=>{
      const source=this.project(actor,project,['owner']);
      this.require(actor,destination,['owner']);
      if(source.organization===destination) return;
      const active=this.db.query<{n:number},[string]>("SELECT count(*) n FROM provision_jobs j JOIN environments e ON e.id=j.environment WHERE e.project=? AND j.state='running'").get(project);
      if(active?.n) throw new Error('Provisioning is active');
      this.db.query('UPDATE projects SET organization=? WHERE id=?').run(destination,project);
      this.record(actor,'project.ownership_changed',project,{from:source.organization,to:destination});
    }).immediate();
  }
  runtimeRouting(runtime:string):RuntimeRouting {
    const row=this.db.query<{revision:number;maintenance:number;placement:string|null},[string]>(
      'SELECT revision,maintenance,placement FROM runtime_routing WHERE runtime=?').get(runtime);
    if(!row)return {revision:0,maintenance:false,placement:null};
    return {revision:row.revision,maintenance:row.maintenance===1,
      placement:row.placement===null?null:validatePlacement(JSON.parse(row.placement))};
  }
  /** Trusted operator only. No HTTP exposure; source fencing and drain are separate prerequisites. */
  changeRuntimeRouting(runtime:string,expectedRevision:number,action:'pause'|'stage'|'resume',placement?:RuntimePlacement):number {
    if(!Number.isSafeInteger(expectedRevision)||expectedRevision<0)throw new Error('Invalid routing revision');
    if(!['pause','stage','resume'].includes(action))throw new Error('Invalid routing action');
    const target=action==='stage'?validatePlacement(placement!):undefined;
    if(action!=='stage'&&placement!==undefined)throw new Error('Unexpected placement');
    return this.db.transaction(()=>{
      if(!this.runtimeReady(runtime))throw new Error('Runtime unavailable');
      const current=this.runtimeRouting(runtime);
      if(current.revision!==expectedRevision)throw new Error('Stale routing revision');
      if(action==='pause'&&current.maintenance||action!=='pause'&&!current.maintenance)throw new Error('Invalid routing transition');
      const revision=current.revision+1;
      if(!Number.isSafeInteger(revision))throw new Error('Routing revision exhausted');
      const next=target??current.placement;
      this.db.query(`INSERT INTO runtime_routing(runtime,revision,maintenance,placement) VALUES (?,?,?,?)
        ON CONFLICT(runtime) DO UPDATE SET revision=excluded.revision,maintenance=excluded.maintenance,placement=excluded.placement`)
        .run(runtime,revision,action==='resume'?0:1,next===null?null:JSON.stringify(next));
      this.record('system:placement','runtime.routing_'+action,runtime,{revision});
      return revision;
    }).immediate();
  }
  close(){this.db.close();}
}

import {Database} from 'bun:sqlite';
import {validatePlacement,type RuntimePlacement,type RuntimeRouting} from './placement';
import {randomUUID,randomBytes} from 'node:crypto';
import {chmodSync} from 'node:fs';

export type MembershipRole = 'owner' | 'admin' | 'viewer';
type Project = {id:string;organization:string;name:string};
type Environment = {id:string;project:string;name:string};
/** One environment with its provisioning state, so a listing needs no per row request. */
export type EnvironmentStatus = Environment&{state:string|null;attempt:number|null;failure:ProvisionFailure|null};
export type ProvisionFailure = 'capacity_exceeded' | 'runtime_failed';
export type ProvisionJob = {environment:string;runtime:string;actor:string;organization:string;state:string;attempt:number;claim:string|null;failure:ProvisionFailure|null};

export type NotificationSeverity = 'info' | 'warning' | 'critical';
export type NotificationChannel = 'email' | 'webhook';
export type NotificationOutcome = 'delivered' | 'transient' | 'failed';
export type NotificationKind = keyof typeof NOTIFICATION_DETAIL_KEYS;
/** Closed reason enum. The fine admission reason (`memory_headroom` and the rest) is
 * produced by the admission gates. Exit code 75 is the whole protocol a refused child may
 * publish, so a capacity refusal keeps its fine reason by recording the value the producer
 * published, and records `unrecorded` when no producer value reached this point. */
export type NotificationReason = typeof NOTIFICATION_REASONS[number];
export type NotificationDetail = Record<string,string|number|boolean>;
export type NotificationSubject = {organization?:string;project?:string;environment?:string;runtime?:string};
export type NotificationClaim = {
  event:string; channel:NotificationChannel; claim:string; attempts:number;
  kind:NotificationKind; severity:NotificationSeverity; subject:NotificationSubject;
  actor:string; reason:NotificationReason; detail:NotificationDetail;
  at:number; last_at:number; occurrences:number; window_until:number;
};
type NotificationDue = {
  event:string; channel:NotificationChannel; attempts:number; kind:NotificationKind;
  severity:NotificationSeverity; organization:string|null; project:string|null;
  environment:string|null; runtime:string|null; actor:string; reason:NotificationReason;
  detail:string; at:number; last_at:number; occurrences:number; window_until:number;
};
export type NotificationSummary = {
  id:string; kind:NotificationKind; severity:NotificationSeverity; at:number; last_at:number;
  occurrences:number; reason:NotificationReason; window_until:number; organization:string|null;
  project:string|null; environment:string|null; runtime:string|null; channel:NotificationChannel;
  state:string; attempts:number; last_error:string|null;
};

/** Every kind of the inventory that an observable in this catalog can produce today. The
 * last three groups are produced outside the catalog, by the Python producers in
 * lab/notification_producers.py, which write through the same outbox rows. Detail keys are
 * closed per kind: a caller cannot add a field, so it cannot add a secret. */
const NOTIFICATION_DETAIL_KEYS = {
  'provision.failed':['failure','attempt'],
  'provision.capacity_refused':['failure','attempt','reason_source'],
  'provision.retry_limit':['attempt','reason_source'],
  'provision.retried':['attempt','reason_source'],
  'routing.paused':['revision'],
  'routing.resumed':['revision'],
  'membership.owner_changed':['target','role'],
  'project.ownership_changed':['from','to'],
  'notifier.channel_failed':['channel','last_error'],
  'notifier.redaction_refused':['refused_event','refused_kind'],
  'installation.started':['stage'],
  'installation.stopped':['stage'],
  'installation.start_failed':['stage'],
  'worker.restart':['restarts'],
  'worker.restart_limit':['restarts'],
  'fence.applied':['phase'],
  'fence.released':['phase'],
  'backup.export_completed':['phase'],
  'backup.export_failed':['phase'],
  'backup.completed':['environments'],
  'backup.failed':['failed'],
  'restore.verified':['status'],
  'restore.failed':['status'],
} satisfies Record<string,string[]>;
const NOTIFICATION_REASONS = [
  'runtime_failed','retry_limit','retry_requested','owner_changed','ownership_changed',
  'routing_paused','routing_resumed','installation_limit','memory_headroom','disk_headroom',
  'inode_headroom','measurement_unavailable','cpu_some10','io_full10','memory_full10',
  'connection_budget','unrecorded','webhook_unreachable','webhook_timeout','webhook_status',
  'smtp_refused','smtp_temporary_failure','channel_disabled','redaction_refused',
  'operator_request','installation_failed','worker_restart','worker_restart_limit',
  'export_completed','export_failed','restore_verified','restore_failed'] as const;
const NOTIFICATION_MAX_ATTEMPTS = 8;
const NOTIFICATION_WINDOW_SECONDS:Record<NotificationSeverity,number> = {info:3600,warning:1800,critical:300};
const NOTIFICATION_BACKOFF_SECONDS = [15,60,300,1800,7200];
const NOTIFICATION_RETENTION_MS = 30*24*60*60*1000;
const NOTIFICATION_LEASE_MS = 30000;
const NOTIFICATION_SUBJECT_PREFIX = 'e_';
/** Fail closed: a shape here means no bytes leave the process and the delivery settles failed. */
const CREDENTIAL_SHAPES:RegExp[] = [
  /postgres(ql)?:\/\//i, /sb_publishable_/i, /sb_secret_/i, /password=/i, /apikey/i,
  /authorization:/i, /BEGIN PRIVATE KEY/, /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/,
  /(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])/, /[A-Za-z0-9_-]{32,}/,
];
/** Catalog identifiers are not credentials. Remove exactly those shapes before scanning,
 * so a uuid or a runtime identifier cannot be mistaken for a key and silence a message. */
const NOTIFICATION_IDENTIFIER = /e_[a-f0-9]{24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g;
function credentialShape(value:string):boolean {
  const normalized = value.replace(NOTIFICATION_IDENTIFIER,'id');
  return CREDENTIAL_SHAPES.some(shape=>shape.test(normalized));
}

/** Internal control-plane boundary. Actor IDs must come from verified management
 * authentication, never request bodies or application JWTs. Not an HTTP API.
 * Placement and runtime credentials deliberately do not belong to ownership.
 */
/** Environments one installation may hold. Mirrors ENVIRONMENT_LIMIT in lab/durable_runtime.py,
 * which stays the enforcing check; this one refuses before a job is queued, so the request
 * answers 409 instead of queueing work the worker must then refuse. */
export const ENVIRONMENT_LIMIT=4;

/** Bumped with each step of Catalog.migrate(). */
export const CATALOG_SCHEMA_VERSION=2;

export class Catalog {
  private db:Database;
  private channels:NotificationChannel[];
  constructor(path:string,options?:{channels?:NotificationChannel[]}) {
    this.db=new Database(path,{create:true,strict:true});
    if(path!==':memory:') chmodSync(path,0o600);
    this.channels=options?.channels??['email','webhook'];
    if(!this.channels.length||this.channels.some(channel=>!['email','webhook'].includes(channel)))
      throw new Error('Invalid notification channel');
    this.channels=[...new Set(this.channels)];
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
      CREATE TABLE IF NOT EXISTS notification_outbox(
        id TEXT PRIMARY KEY,
        at INTEGER NOT NULL,
        last_at INTEGER NOT NULL,
        window_until INTEGER NOT NULL,
        occurrences INTEGER NOT NULL DEFAULT 1,
        digest_sent INTEGER NOT NULL DEFAULT 0 CHECK(digest_sent IN (0,1)),
        kind TEXT NOT NULL,
        severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
        dedupe_key TEXT NOT NULL,
        organization TEXT,
        project TEXT,
        environment TEXT,
        runtime TEXT,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        detail TEXT NOT NULL,
        expires_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS notification_delivery(
        event TEXT NOT NULL REFERENCES notification_outbox(id),
        channel TEXT NOT NULL CHECK(channel IN ('email','webhook')),
        state TEXT NOT NULL CHECK(state IN ('pending','claimed','delivered','failed')),
        attempts INTEGER NOT NULL DEFAULT 0,
        claim TEXT,
        claim_at INTEGER,
        next_attempt_at INTEGER NOT NULL,
        last_error TEXT,
        delivered_at INTEGER,
        PRIMARY KEY(event,channel));
      CREATE INDEX IF NOT EXISTS notification_due
        ON notification_delivery(state,next_attempt_at);
      CREATE INDEX IF NOT EXISTS notification_windows
        ON notification_outbox(dedupe_key,window_until);
      CREATE INDEX IF NOT EXISTS projects_organization ON projects(organization);
      CREATE INDEX IF NOT EXISTS environments_project ON environments(project);
      CREATE INDEX IF NOT EXISTS memberships_actor ON memberships(actor);
      CREATE INDEX IF NOT EXISTS provision_jobs_state ON provision_jobs(state);
      CREATE INDEX IF NOT EXISTS notification_outbox_at ON notification_outbox(at);
      CREATE INDEX IF NOT EXISTS notification_outbox_expiry ON notification_outbox(expires_at);`);
    this.migrate();
  }
  /** One ladder, one transaction, recorded in PRAGMA user_version. A catalog written by a
   * newer release is refused rather than read with rules it was not written for. */
  private migrate() {
    this.db.transaction(()=>{
      const version=this.db.query<{user_version:number},[]>('PRAGMA user_version').get()!.user_version;
      if(version>CATALOG_SCHEMA_VERSION)throw new Error('Catalog schema is newer than this release');
      if(version<1) {
        const columns=this.db.query<{name:string},[]>('PRAGMA table_info(provision_jobs)').all();
        if(!columns.some(column=>column.name==='failure'))
          this.db.exec("ALTER TABLE provision_jobs ADD COLUMN failure TEXT CHECK(failure IS NULL OR failure IN ('capacity_exceeded','runtime_failed'))");
      }
      if(version<2) {
        // Project names become unique per organization. A catalog that already holds a
        // duplicate keeps working: the index waits at version 1 until the names differ, and
        // createProject and transferProject refuse new duplicates either way.
        const duplicate=this.db.query('SELECT 1 FROM projects GROUP BY organization,name HAVING count(*)>1 LIMIT 1').get();
        if(duplicate){this.db.exec('PRAGMA user_version=1');return;}
        this.db.exec('CREATE UNIQUE INDEX IF NOT EXISTS projects_organization_name ON projects(organization,name)');
      }
      this.db.exec(`PRAGMA user_version=${CATALOG_SCHEMA_VERSION}`);
    }).immediate();
  }
  /** Counts the environments that hold or may take a runtime slot: queued, running or ready. */
  private requireEnvironmentCapacity() {
    const held=this.db.query<{n:number},[]>("SELECT count(*) n FROM provision_jobs WHERE state IN ('queued','running','succeeded')").get()!.n;
    if(held>=ENVIRONMENT_LIMIT)throw new Error('Environment capacity reached');
  }
  schemaVersion():number {
    return this.db.query<{user_version:number},[]>('PRAGMA user_version').get()!.user_version;
  }
  private unusedProjectName(organization:string,name:string,except?:string) {
    const clash=this.db.query<{id:string},[string,string]>('SELECT id FROM projects WHERE organization=? AND name=?').get(organization,name);
    if(clash&&clash.id!==except)throw new Error('Name already used');
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
  /** An unknown environment answers Forbidden, exactly like one outside the actor's roles. */
  private environmentProject(actor:string,environment:string,roles:MembershipRole[]):Project {
    const env=this.db.query<Environment,[string]>('SELECT * FROM environments WHERE id=?').get(environment);
    if(!env) throw new Error('Forbidden');
    return this.project(actor,env.project,roles);
  }
  private job(environment:string):ProvisionJob|null {
    return this.db.query<ProvisionJob,[string]>('SELECT * FROM provision_jobs WHERE environment=?').get(environment);
  }
  private record(actor:string,action:string,subject:string,detail:object) {
    this.db.query('INSERT INTO audit_events(actor,action,subject,detail,at) VALUES (?,?,?,?,?)')
      .run(actor,action,subject,JSON.stringify(detail),Date.now());
  }
  /** Enqueue one operator event in the caller's transaction. No I/O, no network, no lock:
   * it issues local SQLite statements exactly like record(), which is why the outbox row
   * commits with the state change that produced it and can never outlive it. Detail keys
   * are closed per kind and every value is scanned for credential shapes, so a caller
   * cannot pass a secret into a message by adding a field. */
  private notify(kind:NotificationKind,severity:NotificationSeverity,dedupeKey:string,subject:NotificationSubject,
    actor:string,reason:NotificationReason,detail:NotificationDetail):string {
    if(!NOTIFICATION_DETAIL_KEYS[kind])throw new Error('Invalid notification kind');
    if(!['info','warning','critical'].includes(severity))throw new Error('Invalid notification severity');
    if(!NOTIFICATION_REASONS.includes(reason))throw new Error('Invalid notification reason');
    this.actor(actor);
    const key=this.notificationText('dedupe_key',dedupeKey,200);
    const allowed=NOTIFICATION_DETAIL_KEYS[kind];
    const payload:NotificationDetail={};
    for(const [field,value] of Object.entries(detail)) {
      if(!allowed.includes(field))throw new Error('Unexpected notification detail field');
      if(typeof value==='number') {
        if(!Number.isFinite(value))throw new Error('Invalid notification detail value');
        payload[field]=value;continue;
      }
      if(typeof value==='boolean') {payload[field]=value;continue;}
      if(typeof value!=='string')throw new Error('Invalid notification detail value');
      payload[field]=this.notificationText(field,value,200);
    }
    for(const field of allowed)
      if(!(field in payload))throw new Error('Missing notification detail field');
    const organization=this.notificationSubject('organization',subject.organization);
    const project=this.notificationSubject('project',subject.project);
    const environment=this.notificationSubject('environment',subject.environment);
    const runtime=this.notificationSubject('runtime',subject.runtime);
    if(runtime!==null&&!new RegExp('^'+NOTIFICATION_SUBJECT_PREFIX+'[a-f0-9]{24}$').test(runtime))
      throw new Error('Invalid notification subject');
    const serialized=JSON.stringify(payload);
    if(credentialShape(key)||credentialShape(serialized)||[organization,project,environment,runtime]
        .some(value=>value!==null&&credentialShape(value)))
      // Fail closed at the enqueue, in the caller's transaction, before any row exists.
      throw new Error('Notification content refused');
    // The detail reached this point only from the closed key set, so it carries no free text.
    for(const field of allowed) {
      if(field.endsWith('_secret')||field.endsWith('_password')||field.endsWith('_token'))
        throw new Error('Notification content refused');
    }
    const now=Date.now();
    const open=this.db.query<{id:string},[string,number]>(
      'SELECT id FROM notification_outbox WHERE dedupe_key=? AND window_until>? ORDER BY at DESC LIMIT 1').get(key,now);
    if(open) {
      // Suppression is durable: the window lives in the catalog, not in a process.
      this.db.query('UPDATE notification_outbox SET occurrences=occurrences+1,last_at=? WHERE id=?').run(now,open.id);
      return open.id;
    }
    const id=randomUUID();
    this.db.query(`INSERT INTO notification_outbox(id,at,last_at,window_until,occurrences,digest_sent,kind,severity,
      dedupe_key,organization,project,environment,runtime,actor,reason,detail,expires_at) VALUES (?,?,?,?,1,0,?,?,?,?,?,?,?,?,?,?,?)`)
      .run(id,now,now,now+NOTIFICATION_WINDOW_SECONDS[severity]*1000,kind,severity,key,organization,project,
        environment,runtime,actor,reason,serialized,now+NOTIFICATION_RETENTION_MS);
    for(const channel of this.channels)
      this.db.query("INSERT INTO notification_delivery(event,channel,state,attempts,next_attempt_at) VALUES (?,?,'pending',0,?)")
        .run(id,channel,now);
    return id;
  }
  private notificationText(field:string,value:string,maxLength:number):string {
    if(typeof value!=='string'||!value||value.length>maxLength||/[\x00-\x1f]/.test(value))
      throw new Error('Invalid notification text: '+field);
    return value;
  }
  private notificationSubject(field:string,value:string|undefined):string|null {
    if(value===undefined)return null;
    return this.notificationText(field,value,64);
  }
  /** Catalog identifiers only. No application data, no recipient, no secret can enter here. */
  private notificationScope(environment:string,organization:string,runtime:string):NotificationSubject {
    const project=this.db.query<{id:string},[string]>('SELECT project id FROM environments WHERE id=?').get(environment);
    const subject:NotificationSubject={organization,environment,runtime};
    if(project)subject.project=project.id;
    return subject;
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
  /** Owners and admins of the organization created at bootstrap run the installation: they
   * may create organizations. Without a recorded bootstrap nobody may, through the API. */
  installationOperator(actor:string):boolean {
    this.actor(actor);
    return !!this.db.query<{role:string},[string]>(`SELECT m.role role FROM installation_bootstrap b
      JOIN memberships m ON m.organization=b.organization WHERE b.singleton=1 AND m.actor=? AND m.role IN ('owner','admin')`).get(actor);
  }
  /** Owners and admins may see who else can act in their organization. */
  listMembers(actor:string,organization:string):{actor:string;role:MembershipRole}[] {
    this.require(actor,organization,['owner','admin']);
    return this.db.query<{actor:string;role:MembershipRole},[string]>(
      "SELECT actor,role FROM memberships WHERE organization=? ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,actor").all(organization);
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
      // Only the changes that alter who can do what: an owner demoted, removed or added.
      if(previous?.role==='owner'||role==='owner')
        this.notify('membership.owner_changed','critical','membership.owner_changed|'+organization+'|'+target,
          {organization},actor,'owner_changed',{target,role:role??'removed'});
    }).immediate();
  }
  createProject(actor:string,organization:string,name:string):string {
    const title=this.name(name),id=randomUUID();
    return this.db.transaction(()=>{
      this.require(actor,organization,['owner','admin']);
      this.unusedProjectName(organization,title);
      this.db.query('INSERT INTO projects VALUES (?,?,?)').run(id,organization,title);
      this.record(actor,'project.created',id,{organization});return id;
    }).immediate();
  }
  createEnvironment(actor:string,project:string,name:string):string {
    const title=this.name(name),id=randomUUID();
    return this.db.transaction(()=>{
      const parent=this.project(actor,project,['owner','admin']);
      if(this.db.query('SELECT 1 FROM environments WHERE project=? AND name=?').get(project,title))
        throw new Error('Name already used');
      this.requireEnvironmentCapacity();
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
  listEnvironments(actor:string,project:string):EnvironmentStatus[] {
    return this.db.transaction(()=>{
      this.project(actor,project,['owner','admin','viewer']);
      return this.db.query<EnvironmentStatus,[string]>(`SELECT e.id,e.project,e.name,j.state,j.attempt,j.failure
        FROM environments e LEFT JOIN provision_jobs j ON j.environment=e.id WHERE e.project=? ORDER BY e.id`).all(project);
    })();
  }
  withReadyEnvironment<T>(actor:string,environment:string,write:boolean,operation:(job:ProvisionJob)=>T):T {
    return this.db.transaction(()=>{
      this.environmentProject(actor,environment,write?['owner','admin']:['owner','admin','viewer']);
      const job=this.job(environment);
      if(!job||job.state!=='succeeded') throw new Error('Environment is not ready');
      return operation(job);
    }).immediate();
  }
  runtimeReady(runtime:string):boolean {
    return !!this.db.query<{environment:string},[string]>(
      "SELECT environment FROM provision_jobs WHERE runtime=? AND state='succeeded'").get(runtime);
  }
  getProvision(actor:string,environment:string):ProvisionJob {
    this.environmentProject(actor,environment,['owner','admin','viewer']);
    const job=this.job(environment);
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
    // An idle poll must not take the write lock; the transaction below still re-reads.
    if(!this.db.query("SELECT 1 FROM provision_jobs WHERE state='queued' LIMIT 1").get()) return null;
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
  finishProvision(environment:string,claim:string,success:boolean,failure:ProvisionFailure='runtime_failed',
    refusalReason?:NotificationReason) {
    if(!['capacity_exceeded','runtime_failed'].includes(failure)) throw new Error('Invalid provisioning failure code');
    if(refusalReason!==undefined&&!NOTIFICATION_REASONS.includes(refusalReason)) throw new Error('Invalid notification reason');
    return this.db.transaction(()=>{
      const result=this.db.query("UPDATE provision_jobs SET state=?,failure=?,claim=NULL WHERE environment=? AND claim=? AND state='running'")
        .run(success?'succeeded':'failed',success?null:failure,environment,claim);
      if(result.changes!==1) throw new Error('Stale provisioning claim');
      this.record('system',success?'provision.succeeded':'provision.failed',environment,success?{}:{failure});
      if(success)return;
      const job=this.job(environment);
      if(!job)return;
      const subject=this.notificationScope(environment,job.organization,job.runtime);
      if(failure==='runtime_failed')
        this.notify('provision.failed','critical','provision.failed|'+environment,subject,'system','runtime_failed',
          {failure,attempt:job.attempt});
      else
        // The coarse exit code is all the child may publish. A coarse refusal keeps its fine
        // reason when the producer recorded it, and says `unrecorded` when none arrived.
        this.notify('provision.capacity_refused','warning','provision.capacity_refused|'+environment,subject,'system',
          refusalReason??'unrecorded',
          {failure,attempt:job.attempt,reason_source:refusalReason?'settlement':'unrecorded'});
    }).immediate();
  }
  /** Worker-only durable outcome settlement. Receipt consumption happens after this commit. */
  applyProvisionReceipt(environment:string,runtime:string,claim:string,attempt:number,exitCode:number,
    refusalReason?:NotificationReason) {
    if(![0,75].includes(exitCode))throw new Error('Unresolved provisioning outcome');
    this.db.transaction(()=>{
      const job=this.job(environment);
      if(!job||job.runtime!==runtime||job.attempt<attempt)throw new Error('Provisioning receipt mismatch');
      const success=exitCode===0;
      const prior=this.db.query<{runtime:string;claim:string;exit_code:number},[string,number]>(
        'SELECT runtime,claim,exit_code FROM provision_effect_results WHERE environment=? AND attempt=?').get(environment,attempt);
      if(prior) {
        if(prior.runtime!==runtime||prior.claim!==claim||prior.exit_code!==exitCode)throw new Error('Provisioning receipt mismatch');
        return;
      }
      if(job.attempt!==attempt||job.state!=='running'||job.claim!==claim)throw new Error('Provisioning receipt mismatch');
      this.finishProvision(environment,claim,success,'capacity_exceeded',refusalReason);
      this.db.query('INSERT INTO provision_effect_results(environment,attempt,runtime,claim,exit_code) VALUES (?,?,?,?,?)')
        .run(environment,attempt,runtime,claim,exitCode);
    }).immediate();
  }
  /** Fresh worker/effect/operation ownership and preflight proof are required by the caller. */
  recoverPreflightReceipt(environment:string,runtime:string,claim:string,attempt:number,token:string):'requeued'|'failed' {
    return this.db.transaction(()=>{
      const job=this.job(environment);
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
      if(!retry)
        // The retry limit is exactly the case the operator must hear about.
        this.notify('provision.retry_limit','critical','provision.retry_limit|'+environment,
          this.notificationScope(environment,job.organization,job.runtime),'system','retry_limit',
          {attempt,reason_source:'settlement'});
      return retry?'requeued':'failed';
    }).immediate();
  }
  retryProvision(actor:string,environment:string) {
    this.db.transaction(()=>{
      const parent=this.environmentProject(actor,environment,['owner','admin']);
      this.requireEnvironmentCapacity();
      const result=this.db.query("UPDATE provision_jobs SET state='queued',actor=?,organization=?,claim=NULL,failure=NULL WHERE environment=? AND state IN ('failed','cancelled')")
        .run(actor,parent.organization,environment);
      if(result.changes!==1) throw new Error('Operation is not retryable');
      this.record(actor,'provision.retried',environment,{});
      const job=this.job(environment);
      if(job)
        this.notify('provision.retried','info','provision.retried|'+environment,
          this.notificationScope(environment,parent.organization,job.runtime),actor,'retry_requested',
          {attempt:job.attempt,reason_source:'settlement'});
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
      this.unusedProjectName(destination,source.name,project);
      const active=this.db.query<{n:number},[string]>("SELECT count(*) n FROM provision_jobs j JOIN environments e ON e.id=j.environment WHERE e.project=? AND j.state='running'").get(project);
      if(active?.n) throw new Error('Provisioning is active');
      this.db.query('UPDATE projects SET organization=? WHERE id=?').run(destination,project);
      // A queued job carries the requester's authority in the source organization, so the
      // move cancels it here, visibly, instead of leaving the claim to find the mismatch. Every
      // job of the project then names the destination, so later events reach the new owners.
      const queued=this.db.query<{environment:string},[string]>(
        "SELECT j.environment environment FROM provision_jobs j JOIN environments e ON e.id=j.environment WHERE e.project=? AND j.state='queued'").all(project);
      for(const job of queued) {
        this.db.query("UPDATE provision_jobs SET state='cancelled',claim=NULL WHERE environment=?").run(job.environment);
        this.record('system','provision.cancelled',job.environment,{reason:'project_transferred'});
      }
      this.db.query('UPDATE provision_jobs SET organization=? WHERE environment IN (SELECT id FROM environments WHERE project=?)')
        .run(destination,project);
      this.record(actor,'project.ownership_changed',project,{from:source.organization,to:destination});
      this.notify('project.ownership_changed','critical','project.ownership_changed|'+project,
        {organization:source.organization,project},actor,'ownership_changed',
        {from:source.organization,to:destination});
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
      if(action!=='stage') {
        const job=this.db.query<ProvisionJob,[string]>('SELECT * FROM provision_jobs WHERE runtime=?').get(runtime);
        if(job)
          this.notify(action==='pause'?'routing.paused':'routing.resumed',
            action==='pause'?'warning':'info','runtime.routing_'+action+'|'+runtime,
            this.notificationScope(job.environment,job.organization,job.runtime),'system:placement',
            action==='pause'?'routing_paused':'routing_resumed',{revision});
      }
      return revision;
    }).immediate();
  }
  /** One claimant, exactly once. Mirrors claimProvision: one immediate transaction, select
   * candidates, guard the update, return the claimed rows. Only the process holding the
   * installation worker lock calls this, so no lease stealing rule is needed. */
  claimNotifications(limit=20,leaseMs=NOTIFICATION_LEASE_MS):NotificationClaim[] {
    if(!Number.isInteger(limit)||limit<1||limit>200)throw new Error('Invalid notification limit');
    if(!Number.isInteger(leaseMs)||leaseMs<1000)throw new Error('Invalid notification lease');
    const now=Date.now();
    return this.db.transaction(()=>{
      const rows=this.db.query<NotificationDue,[number,number,number]>(`SELECT d.event event,d.channel channel,
        d.attempts attempts,o.kind kind,o.severity severity,o.organization organization,o.project project,
        o.environment environment,o.runtime runtime,o.actor actor,o.reason reason,o.detail detail,o.at at,
        o.last_at last_at,o.occurrences occurrences,o.window_until window_until
        FROM notification_delivery d JOIN notification_outbox o ON o.id=d.event
        WHERE (d.state='pending' AND d.next_attempt_at<=?) OR (d.state='claimed' AND d.claim_at<=?)
        ORDER BY o.at LIMIT ?`).all(now,now-leaseMs,limit);
      const claims:NotificationClaim[]=[];
      for(const row of rows) {
        const claim=randomUUID();
        const result=this.db.query(`UPDATE notification_delivery SET state='claimed',claim=?,claim_at=?,attempts=attempts+1
          WHERE event=? AND channel=? AND state IN ('pending','claimed')`).run(claim,now,row.event,row.channel);
        if(result.changes!==1)continue;
        let detail:NotificationDetail;
        try{detail=JSON.parse(row.detail);}catch{throw new Error('Invalid notification detail');}
        const subject:NotificationSubject={};
        if(row.organization)subject.organization=row.organization;
        if(row.project)subject.project=row.project;
        if(row.environment)subject.environment=row.environment;
        if(row.runtime)subject.runtime=row.runtime;
        claims.push({event:row.event,channel:row.channel,claim,attempts:row.attempts+1,kind:row.kind,
          severity:row.severity,subject,actor:row.actor,reason:row.reason,detail,at:row.at,last_at:row.last_at,
          occurrences:row.occurrences,window_until:row.window_until});
      }
      return claims;
    }).immediate();
  }
  /** Settlement mirrors the stale claim guard of finishProvision. Only the attempt holding
   * the current claim can move a row, so a replay of an older attempt is a stale claim and
   * is rejected. It never touches provision_jobs, audit_events or any operation state. */
  settleNotification(event:string,channel:NotificationChannel,claim:string,outcome:NotificationOutcome,error:string|null=null) {
    if(!['delivered','transient','failed'].includes(outcome))throw new Error('Invalid notification outcome');
    if(error!==null&&(typeof error!=='string'||error.length>60||!/^[a-z0-9_]+$/.test(error)))
      throw new Error('Invalid notification error token');
    const now=Date.now();
    this.db.transaction(()=>{
      if(outcome==='delivered') {
        const result=this.db.query(`UPDATE notification_delivery SET state='delivered',delivered_at=?,claim=NULL,
          last_error=NULL,next_attempt_at=? WHERE event=? AND channel=? AND claim=? AND state='claimed'`)
          .run(now,now,event,channel,claim);
        if(result.changes!==1)throw new Error('Stale notification claim');
        return;
      }
      const row=this.db.query<{attempts:number},[string,string]>(
        'SELECT attempts FROM notification_delivery WHERE event=? AND channel=?').get(event,channel);
      if(!row)throw new Error('Stale notification claim');
      // A permanent refusal, or an exhausted retry budget, settles failed and stays visible.
      const permanent=outcome==='failed'||row.attempts>=NOTIFICATION_MAX_ATTEMPTS;
      const next=permanent?now:now+this.notificationBackoffMs(row.attempts);
      const result=this.db.query(`UPDATE notification_delivery SET state=?,claim=NULL,last_error=?,next_attempt_at=?
        WHERE event=? AND channel=? AND claim=? AND state='claimed'`)
        .run(permanent?'failed':'pending',error,next,event,channel,claim);
      if(result.changes!==1)throw new Error('Stale notification claim');
    }).immediate();
  }
  private notificationBackoffMs(attempts:number):number {
    const index=Math.min(Math.max(attempts,1),NOTIFICATION_BACKOFF_SECONDS.length)-1;
    return (NOTIFICATION_BACKOFF_SECONDS[index]??NOTIFICATION_BACKOFF_SECONDS[0]!)*1000;
  }
  /** Retention, bounded per iteration. A row is pruned only when no delivery is pending or
   * claimed, so an undelivered event is never deleted: it ages visibly instead. */
  pruneNotifications(now=Date.now(),limit=500):number {
    if(!Number.isInteger(limit)||limit<1||limit>5000)throw new Error('Invalid notification limit');
    return this.db.transaction(()=>{
      const rows=this.db.query<{id:string},[number,number]>(`SELECT o.id FROM notification_outbox o
        WHERE o.expires_at < ? AND NOT EXISTS(SELECT 1 FROM notification_delivery d
          WHERE d.event=o.id AND d.state IN ('pending','claimed')) ORDER BY o.expires_at LIMIT ?`).all(now,limit);
      for(const row of rows) {
        this.db.query('DELETE FROM notification_delivery WHERE event=?').run(row.id);
        this.db.query('DELETE FROM notification_outbox WHERE id=?').run(row.id);
      }
      return rows.length;
    }).immediate();
  }
  /** Read-only delivery state. Already safe fields only: no recipient, no rendered body.
   * With an actor, only the events that actor may see: an event belongs to its organization,
   * or, when a producer recorded only a runtime or an environment, to the organization that
   * owns it now. Owners and admins of that organization see it. An event that belongs to no
   * organization (installation start, worker restarts) is shown to owners and admins of the
   * organization created at installation bootstrap, or of any organization while no
   * bootstrap is recorded (a lab catalog). Without an actor: every event, for trusted callers. */
  listNotifications(limit=50,actor?:string) {
    if(!Number.isInteger(limit)||limit<1||limit>500)throw new Error('Invalid notification limit');
    const columns=`o.id id,o.kind kind,o.severity severity,o.at at,
      o.last_at last_at,o.occurrences occurrences,o.reason reason,o.window_until window_until,
      o.organization organization,o.project project,o.environment environment,o.runtime runtime,
      d.channel channel,d.state state,d.attempts attempts,d.last_error last_error
      FROM notification_outbox o JOIN notification_delivery d ON d.event=o.id`;
    if(actor===undefined)
      return this.db.query<NotificationSummary,[number]>(`SELECT ${columns}
        ORDER BY o.at DESC,d.channel LIMIT ?`).all(limit);
    this.actor(actor);
    return this.db.query<NotificationSummary,[string,string,number]>(`WITH scoped AS (SELECT ${columns.replace(
      'FROM notification_outbox',`,COALESCE(o.organization,
        (SELECT p.organization FROM provision_jobs j JOIN environments e ON e.id=j.environment
          JOIN projects p ON p.id=e.project WHERE j.runtime=o.runtime),
        (SELECT p.organization FROM environments e JOIN projects p ON p.id=e.project WHERE e.id=o.environment),
        (SELECT p.organization FROM projects p WHERE p.id=o.project)) scope
      FROM notification_outbox`)}),
      mine AS (SELECT organization FROM memberships WHERE actor=? AND role IN ('owner','admin'))
      SELECT id,kind,severity,at,last_at,occurrences,reason,window_until,organization,project,environment,runtime,
        channel,state,attempts,last_error FROM scoped
      WHERE scope IN (SELECT organization FROM mine)
        OR (scope IS NULL AND (
          (SELECT organization FROM installation_bootstrap WHERE singleton=1) IN (SELECT organization FROM mine)
          OR (NOT EXISTS (SELECT 1 FROM installation_bootstrap) AND EXISTS (SELECT 1 FROM memberships WHERE actor=? AND role IN ('owner','admin')))))
      ORDER BY at DESC,channel LIMIT ?`).all(actor,actor,limit);
  }
  close(){this.db.close();}
}

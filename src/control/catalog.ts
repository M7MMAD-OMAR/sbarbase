import {Database} from 'bun:sqlite';
import {randomUUID} from 'node:crypto';
import {chmodSync} from 'node:fs';

export type MembershipRole = 'owner' | 'admin' | 'viewer';
type Project = {id:string;organization:string;name:string};
type Environment = {id:string;project:string;name:string};

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
      CREATE TABLE IF NOT EXISTS memberships(
        organization TEXT NOT NULL REFERENCES organizations(id), actor TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('owner','admin','viewer')),
        PRIMARY KEY(organization,actor));
      CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,
        organization TEXT NOT NULL REFERENCES organizations(id),name TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS environments(id TEXT PRIMARY KEY,
        project TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,
        UNIQUE(project,name));
      CREATE TABLE IF NOT EXISTS audit_events(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
        action TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL, at INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS projects_organization ON projects(organization);
      CREATE INDEX IF NOT EXISTS environments_project ON environments(project);`);
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
  /** Bootstrap entry point for a trusted operator until management auth exists. */
  createOrganization(owner:string,name:string):string {
    this.actor(owner); const title=this.name(name),id=randomUUID();
    return this.db.transaction(()=>{
      this.db.query('INSERT INTO organizations VALUES (?,?)').run(id,title);
      this.db.query('INSERT INTO memberships VALUES (?,?,?)').run(id,owner,'owner');
      this.record(owner,'organization.created',id,{});return id;
    }).immediate();
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
      this.project(actor,project,['owner','admin']);
      this.db.query('INSERT INTO environments VALUES (?,?,?)').run(id,project,title);
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
  /** Metadata-only transfer. Requires owner authority in both organizations.
   * Runtime transfer must additionally revoke/rotate previously exposed access.
   * Do not expose as a complete project transfer until that workflow exists.
   */
  transferProject(actor:string,project:string,destination:string) {
    this.db.transaction(()=>{
      const source=this.project(actor,project,['owner']);
      this.require(actor,destination,['owner']);
      if(source.organization===destination) return;
      this.db.query('UPDATE projects SET organization=? WHERE id=?').run(destination,project);
      this.record(actor,'project.ownership_changed',project,{from:source.organization,to:destination});
    }).immediate();
  }
  close(){this.db.close();}
}

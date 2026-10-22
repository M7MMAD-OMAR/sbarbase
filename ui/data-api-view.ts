/** What the console's Data API section says, from the environment's state (src/control/catalog.ts
 * dataApi). Kept apart from the component so the wording is tested: the console must never call
 * settings applied that PostgREST does not serve. */
export type DataApiSettings={schemas:string[];extraSearchPath:string[];maxRows:number|null};
export type DataApiView=DataApiSettings&{state:'default'|'pending'|'applied'|'failed';failure:string|null;served:DataApiSettings;report:string|null};

const LABELS:Record<DataApiView['state'],string>={default:'Default',pending:'Applying…',applied:'Applied',failed:'Not applied'};
// An empty list of saved schemas turns the Data API off (src/control/catalog.ts dataApi).
/** The platform's empty schema, served when none of the exposed schemas exists (lab/data_api.py). */
const EMPTY='sbarbase_empty';

/** The report (lab/data_api.py not_told_text) of settings that are written and recorded but that PostgREST was not signalled to read. */
const NOT_TOLD='The Data API settings are saved and written, but PostgREST could not be told to reload them. It picks them up at its next reload or restart.';

export function dataApiDisplay(view:DataApiView):{label:string;tone:'applied'|'failed'|'running'|'';notices:string[];servedNow:string|null} {
 // Only a report that says something was left out or not written makes the settings partly served.
 const onlyNotTold=view.report===NOT_TOLD;
 const partly=view.state==='applied'&&!!view.report&&!onlyNotTold;
 if(view.state==='applied'&&!partly&&!view.schemas.length&&view.served.schemas.join()===EMPTY)
  return {label:'Off',tone:'',notices:view.report?[view.report]:[],servedNow:null};
 const tone=partly||view.state==='failed'?'failed':view.state==='applied'?'applied':view.state==='pending'?'running':'';
 const notices=[...(view.state==='failed'&&view.failure?[view.failure]:[]),...(view.report?[view.report]:[])];
 const differs=view.served.schemas.join()!==view.schemas.join()||view.served.extraSearchPath.join()!==view.extraSearchPath.join()
  ||view.served.maxRows!==view.maxRows;
 // The empty schema alone is served for one of two reasons: a start left every exposed schema out
 // (and reported it), or the settings applied last turned the Data API off.
 const shown=view.served.schemas.filter(name=>name!==EMPTY).join(', ')
  ||(view.report&&!onlyNotTold?'no schema, since none of the exposed schemas exists':'no schema, since the Data API is off');
 return {label:partly?'Partly served':LABELS[view.state],tone,notices,servedNow:view.state!=='default'&&(view.state!=='applied'||partly||differs)?shown:null};
}

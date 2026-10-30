/** A labelled on/off row of the Authentication workspace. */
export function Switch({id,checked,disabled,label,hint,onChange}:{id:string;checked:boolean;disabled:boolean;label:string;hint:string;onChange:(value:boolean)=>void}){
 return <div className="aw-row"><div className="aw-text"><label id={id+'-label'} htmlFor={id}>{label}</label><p className="aw-hint">{hint}</p></div>
  <button id={id} type="button" role="switch" aria-checked={checked} aria-labelledby={id+'-label'} disabled={disabled} className="aw-switch" onClick={()=>onChange(!checked)}><span/></button></div>;
}

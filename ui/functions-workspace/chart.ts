export type Bar={x:number;width:number;height:number;y:number};
/** Heights for a bar chart of `values` drawn in a box `width` by `height`: the largest value (or `scale`, so a second series
 * can share the first one's scale) fills the box. */
export function bars(values:number[],width:number,height:number,gap=0.2,scale=Math.max(1,...values)):Bar[] {
 const top=scale,slot=values.length?width/values.length:width;
 return values.map((value,index)=>{const h=value>0?Math.max(1,Math.round(value/top*height*100)/100):0;
  return {x:Math.round(index*slot*100)/100,width:Math.round(slot*(1-gap)*100)/100,height:h,y:Math.round((height-h)*100)/100};});
}
/** Points of a line through `values` (a null is a gap); the largest value touches the top. */
export function line(values:(number|null)[],width:number,height:number):string[] {
 const top=Math.max(1,...values.map(value=>value??0)),step=values.length>1?width/(values.length-1):0;
 const segments:string[]=[];let current:string[]=[];
 values.forEach((value,index)=>{
  if(value===null){if(current.length)segments.push(current.join(' '));current=[];return;}
  current.push(`${Math.round(index*step*100)/100},${Math.round((height-value/top*height)*100)/100}`);
 });
 if(current.length)segments.push(current.join(' '));
 return segments;
}

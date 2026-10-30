/** Plain text for numbers and times the Functions pages show. Pure, so the wording is tested. */
export function ago(then:number,now=Date.now()):string {
 const seconds=Math.max(0,Math.round((now-then)/1000));
 if(seconds<60)return 'just now';
 const plural=(count:number,unit:string)=>`${count} ${unit}${count===1?'':'s'} ago`;
 const minutes=Math.floor(seconds/60);if(minutes<60)return plural(minutes,'minute');
 const hours=Math.floor(minutes/60);if(hours<24)return plural(hours,'hour');
 const days=Math.floor(hours/24);if(days<30)return plural(days,'day');
 const months=Math.floor(days/30);if(months<12)return months===1?'a month ago':plural(months,'month');
 const years=Math.floor(days/365);return years<=1?'a year ago':plural(years,'year');
}
export function bytes(value:number):string {
 if(value<1024)return `${value} B`;
 if(value<1024*1024)return `${Math.round(value/102.4)/10} KiB`;
 return `${Math.round(value/104857.6)/10} MiB`;
}
export function milliseconds(value:number|null):string {
 if(value===null)return 'none';
 return value<1000?`${value} ms`:`${Math.round(value/100)/10} s`;
}
export function percent(part:number,total:number):string {return total?`${Math.round(part/total*1000)/10}%`:'0%';}
/** 12 hour time with Western digits, whatever the browser's language: 7:08:05 PM. */
export function clock(at:number):string {
 const date=new Date(at),hours=date.getHours(),two=(value:number)=>String(value).padStart(2,'0');
 return `${hours%12||12}:${two(date.getMinutes())}:${two(date.getSeconds())} ${hours<12?'AM':'PM'}`;
}

export type FunctionRow={name:string;verify_jwt:boolean;updated_at:number;created_at:number;deployments:number;size:number;path:string};
export type SortKey='name'|'created'|'updated';
/** The list the page shows: names containing the search text, ordered by one column. */
export function visibleFunctions(rows:FunctionRow[],query:string,key:SortKey,descending:boolean):FunctionRow[] {
 const needle=query.trim().toLowerCase();
 const order={name:(a:FunctionRow,b:FunctionRow)=>a.name.localeCompare(b.name),created:(a:FunctionRow,b:FunctionRow)=>a.created_at-b.created_at,
  updated:(a:FunctionRow,b:FunctionRow)=>a.updated_at-b.updated_at}[key];
 return rows.filter(row=>!needle||row.name.toLowerCase().includes(needle)).sort((a,b)=>(descending?-1:1)*order(a,b)||a.name.localeCompare(b.name));
}

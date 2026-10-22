import {test,expect} from 'bun:test';
import {dataApiDisplay,type DataApiView} from '../ui/data-api-view';

// The console's Data API wording (ui/DataApi.tsx): it never calls settings applied that PostgREST does not serve.
const settings={schemas:['public','app'],extraSearchPath:['public'],maxRows:null};
const view=(change:Partial<DataApiView>):DataApiView=>({...settings,state:'applied',failure:null,served:settings,report:null,...change});

test('applied and served alike: Applied, nothing else',()=>{
 expect(dataApiDisplay(view({}))).toEqual({label:'Applied',tone:'applied',notices:[],servedNow:null});
});
test('a start that left a schema out: Partly served, with its report and what is served',()=>{
 const shown=dataApiDisplay(view({report:'Not served because missing from the database: app.',served:{...settings,schemas:['public']}}));
 expect(shown).toEqual({label:'Partly served',tone:'failed',notices:['Not served because missing from the database: app.'],servedNow:'public'});
});
test('pending: Applying, and what is served meanwhile',()=>{
 expect(dataApiDisplay(view({state:'pending',served:{...settings,schemas:['public']}}))).toMatchObject({label:'Applying…',tone:'running',servedNow:'public'});
});
test('refused: Not applied, the reason and what is still served',()=>{
 const shown=dataApiDisplay(view({state:'failed',failure:'The schema app does not exist',served:{...settings,schemas:['public','old']}}));
 expect(shown).toEqual({label:'Not applied',tone:'failed',notices:['The schema app does not exist'],servedNow:'public, old'});
});
test('nothing left: the empty schema is never named',()=>{
 expect(dataApiDisplay(view({report:'x',served:{...settings,schemas:['sbarbase_empty']}})).servedNow).toBe('no schema, since none of the exposed schemas exists');
});
test('applied but serving something else, as after a restore raced a change: what is served shows',()=>{
 expect(dataApiDisplay(view({served:{...settings,schemas:['public']}})).servedNow).toBe('public');
});
test('defaults: nothing to explain',()=>{
 expect(dataApiDisplay(view({state:'default',...{schemas:['public']},served:{...settings,schemas:['public']}}))).toMatchObject({label:'Default',servedNow:null});
});
test('turned off: Off, nothing served and nothing to explain',()=>{
 expect(dataApiDisplay(view({schemas:[],served:{...settings,schemas:['sbarbase_empty']}}))).toEqual({label:'Off',tone:'',notices:[],servedNow:null});
});
test('a change saved after turning it off: what is served says the Data API is off, not that a schema is missing',()=>{
 const off={...settings,schemas:['sbarbase_empty']};
 expect(dataApiDisplay(view({state:'pending',served:off})).servedNow).toBe('no schema, since the Data API is off');
 expect(dataApiDisplay(view({state:'failed',failure:'The schema app does not exist',served:off})).servedNow).toBe('no schema, since the Data API is off');
});

test('written but PostgREST not signalled: still Applied, with the notice, and never "none exists"',()=>{
 const told='The Data API settings are saved and written, but PostgREST could not be told to reload them. It picks them up at its next reload or restart.';
 expect(dataApiDisplay(view({report:told}))).toEqual({label:'Applied',tone:'applied',notices:[told],servedNow:null});
 const off={...settings,schemas:['sbarbase_empty']};
 expect(dataApiDisplay(view({schemas:[],report:told,served:off}))).toEqual({label:'Off',tone:'',notices:[told],servedNow:null});
 expect(dataApiDisplay(view({state:'pending',report:told,served:off})).servedNow).toBe('no schema, since the Data API is off');
 // Together with a schema left out, it is partly served.
 expect(dataApiDisplay(view({report:'Not served because missing from the database: app. '+told,served:{...settings,schemas:['public']}})).label).toBe('Partly served');
});

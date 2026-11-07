// SPDX-License-Identifier: Apache-2.0
/** Neutral protocol fixtures only. Never starts FreeCAD, captures CAD or writes an export. */
import assert from 'node:assert/strict';
import { test, type TestContext } from 'node:test';
import { createServer as httpServer } from 'node:http';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chmodSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Client, InMemoryTransport } from '@modelcontextprotocol/client';
import { createServer, VERSION } from '../src/server.js';
import { PftBridgeError } from '../src/adapters/pft-freecad-client.js';
import { PFT_DRAWING_EXPORT_CAPABILITY, PftDrawingExportCapabilitySchema, PftDrawingExportPrepareInputSchema,
  PftDrawingExportTokenInputSchema, PftDrawingExportDataSchema, pftDrawingExport } from '../src/pft-drawing-export.js';

const methods = ['prepare_drawing_export','request_drawing_export_approval','get_drawing_export_status'] as const;
const args = { document: 'NeutralDocument', page: 'NeutralPage', format: 'pdf' as const, requestId: 'neutral-export' };
const token = 'neutral_pdf_export_token_0001';
const capability = () => ({ supported_methods: [...methods], drawing_export: structuredClone(PFT_DRAWING_EXPORT_CAPABILITY) });
function dto(state = 'preparing'): any {
  const value: any = { document: args.document,page: args.page,format: 'pdf',request_id: args.requestId,token,state,
    expires_at: '2030-01-01T00:00:00+00:00',summary: {document: args.document,page: args.page,source: '',expires_at: '2030-01-01T00:00:00+00:00'},
    byte_count: null,sha256: null,target: null,target_exists: null,capture_state: 'not_captured',publication_state: 'not_published',cleanup_state: 'complete',
    cad_protection_state: 'not_checked',native_capture_confirmation: false,native_publication_confirmation: false,
    storage: 'memory_only',saves_document: false,requires_confirmation: true,error: null };
  if (state !== 'preparing') value.summary = {...value.summary,source: 'NeutralSource',view_count: 3,dimension_count: 0,
    page_open: true,paper_mm: [297,210],projection_verification: 'not_checked',manual_review_required: true};
  if (['rendering','preview_ready','target_ready','publishing','published'].includes(state)) Object.assign(value,
    {byte_count: 512,sha256: 'a'.repeat(64),capture_state: 'captured',cad_protection_state: 'verified',native_capture_confirmation: true});
  if (['target_ready','publishing','published'].includes(state)) Object.assign(value,{target: '/synthetic/neutral.pdf',target_exists: false});
  if (['publishing','published'].includes(state)) value.native_publication_confirmation = true;
  if (state === 'published') value.publication_state = 'published';
  if (['failed','unknown'].includes(state)) value.error = {code: 'DRAWING_EXPORT_FAILED',message: 'Drawing export could not be verified. Inspect its separate capture, publication and cleanup outcomes.'};
  return value;
}

test('PDF导出精确cap、显式参数和独立状态，不接受路径/确认/覆盖', () => {
  assert.deepEqual(PftDrawingExportCapabilitySchema.parse(PFT_DRAWING_EXPORT_CAPABILITY),PFT_DRAWING_EXPORT_CAPABILITY);
  for (const key of Object.keys(PFT_DRAWING_EXPORT_CAPABILITY)) {
    const cap: any = structuredClone(PFT_DRAWING_EXPORT_CAPABILITY); delete cap[key];
    assert.equal(PftDrawingExportCapabilitySchema.safeParse(cap).success,false);
  }
  assert.deepEqual(PftDrawingExportPrepareInputSchema.parse(args),args);
  for (const extra of [{path: '/synthetic/other.pdf'},{confirm: true},{overwrite: false},{instance: 'other'},{format: 'svg'}])
    assert.equal(PftDrawingExportPrepareInputSchema.safeParse({...args,...extra}).success,false);
  for (const key of Object.keys(args)) {const input: any={...args};delete input[key];assert.equal(PftDrawingExportPrepareInputSchema.safeParse(input).success,false);}
  for (const suffix of ['\n','\r','\u200d','\ud800'])
    assert.equal(PftDrawingExportPrepareInputSchema.safeParse({...args,document: args.document+suffix}).success,false);
  for (const extra of [{confirmed: true},{path: '/synthetic/example.pdf'},{overwrite: true}])
    assert.equal(PftDrawingExportTokenInputSchema.safeParse({token,...extra}).success,false);
});

test('完整运行态cap和三个方法缺失时零派发，不退化为脚本', async () => {
  for (const status of [{}, {...capability(),drawing_export: undefined}, {...capability(),drawing_export: {...PFT_DRAWING_EXPORT_CAPABILITY,formats: ['svg']}},
    ...methods.map(method => ({...capability(),supported_methods: methods.filter(item => item!==method)}))]) {
    let count=0;
    await assert.rejects(pftDrawingExport('prepare_drawing_export',args,{status: async()=>status,call: async()=>{count++;return dto();}},PftBridgeError));
    assert.equal(count,0);
  }
});

test('prepare单次映射并绑定requestId，token查询独立于CAD和文件发布', async () => {
  const calls: unknown[]=[];
  const transport={status: async()=>capability(),call: async(method: string,params: unknown)=>{calls.push({method,params});return dto();}};
  assert.deepEqual(await pftDrawingExport('prepare_drawing_export',args,transport,PftBridgeError),dto());
  for (const method of methods.slice(1)) assert.deepEqual(await pftDrawingExport(method,{token},transport,PftBridgeError),dto());
  assert.deepEqual(calls,[{method: methods[0],params: {docName: args.document,pageName: args.page,format: 'pdf',requestId: args.requestId}},
    ...methods.slice(1).map(method=>({method,params: {token}}))]);
});

test('发布与清理/文档保护双轴保留，unknown不转换成未发布或普通失败', () => {
  for (const state of ['preparing','prepared','awaiting_capture','capturing','rendering','preview_ready','target_ready','publishing','published','cancelled','expired','failed','unknown'])
    assert.equal(PftDrawingExportDataSchema.safeParse(dto(state)).success,true,state);
  const published=dto('published');published.cleanup_state='unknown';published.cad_protection_state='unverified';
  published.error={code:'DRAWING_EXPORT_POST_PUBLISH_CHANGED',message:'The file outcome is retained, but CAD protection could not be verified afterwards.'};
  assert.deepEqual(PftDrawingExportDataSchema.parse(published),published);
  const unknown=dto('unknown');unknown.publication_state='unknown';
  assert.deepEqual(PftDrawingExportDataSchema.parse(unknown),unknown);
  for (const mutate of [(v:any)=>{v.native_publication_confirmation=false;},(v:any)=>{v.native_capture_confirmation=false;},
    (v:any)=>{v.publication_state='not_published';},(v:any)=>{v.sha256=null;},(v:any)=>{v.target_exists=null;}]) {
    const value=dto('published');mutate(value);assert.equal(PftDrawingExportDataSchema.safeParse(value).success,false);
  }
});

test('畸形/错绑定/私有错误正文不转发，不自动重新请求', async () => {
  for (const mode of ['document','page','request','token','summary','extra','error','large']) {
    const value=dto();
    if(mode==='document')value.document='Other';if(mode==='page')value.page='Other';if(mode==='request')value.request_id='other';
    if(mode==='token')value.token='other_token_00000001';if(mode==='summary')value.summary.page='Other';
    if(mode==='extra')value.confirmed=true;if(mode==='error')value.error={code:'DRAWING_EXPORT_FAILED',message:'/neutral/private/provider-detail'};
    if(mode==='large')value.summary.source='x'.repeat(17000);
    let calls=0;
    const operation=mode==='token'?'get_drawing_export_status':'prepare_drawing_export';
    await assert.rejects(pftDrawingExport(operation,mode==='token'?{token}:args,{status: async()=>capability(),call: async()=>{calls++;return value;}},PftBridgeError),
      (error:any)=>error.code==='INVALID_RESPONSE'&&!error.message.includes('provider-detail'));
    assert.equal(calls,1);
  }
  let calls=0;
  await assert.rejects(pftDrawingExport('request_drawing_export_approval',{token},{status: async()=>capability(),call: async()=>{calls++;throw new PftBridgeError('BRIDGE_TIMEOUT','raw private transport text');}},PftBridgeError),
    (error:any)=>error.code==='BRIDGE_TIMEOUT'&&!error.message.includes('raw private'));
  assert.equal(calls,1);
});

test('Python真实claim DTO跨语言校验，无FreeCAD/Qt/账号或文件发布', () => {
  const modulePath=fileURLToPath(new URL('../addon/PhoenixFreeCADTools/pft_drawing_export.py',import.meta.url));
  const script=`import importlib.util,json,sys\nspec=importlib.util.spec_from_file_location('neutral_export_contract',sys.argv[1])\nm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\nclass Error(Exception):\n def __init__(self,code,message):self.code=code;super().__init__(message)\nm.begin_approval_lifecycle()\nfresh,data=m.claim_prepare({'docName':'NeutralDocument','pageName':'NeutralPage','format':'pdf','requestId':'neutral-export'},Error)\nassert fresh and 'FreeCAD' not in sys.modules and 'PySide' not in sys.modules\nprint(json.dumps(data))`;
  const result=spawnSync('python3',['-I','-B','-c',script,modulePath],{encoding:'utf8',maxBuffer:32768});
  assert.equal(result.status,0,result.stderr);assert.equal(PftDrawingExportDataSchema.safeParse(JSON.parse(result.stdout)).success,true);
});

test('Python实际固定错误白名单与TS状态合同保持一致，不接原生异常正文', () => {
  const modulePath=fileURLToPath(new URL('../addon/PhoenixFreeCADTools/pft_drawing_export.py',import.meta.url));
  const script=`import importlib.util,json,sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location('neutral_export_errors',sys.argv[1])\nm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\np=importlib.util.spec_from_file_location('neutral_export_profile_errors',Path(sys.argv[1]).with_name('pft_drawing_export_profile.py'))\nprofile=importlib.util.module_from_spec(p);p.loader.exec_module(profile)\nclass Error(Exception):\n def __init__(self,code,message):self.code=code;super().__init__(message)\nm.begin_approval_lifecycle()\n_,data=m.claim_prepare({'docName':'NeutralDocument','pageName':'NeutralPage','format':'pdf','requestId':'neutral-export'},Error)\nrecord=m._records[data['token']];record['state']='failed'\nrows=[]\nfor code in sorted(set(m._MESSAGES)|m._PDF_CODES|m._FILE_CODES|profile.ERROR_CODES):\n record['error_code']=code;rows.append(m._dto(record))\nassert 'FreeCAD' not in sys.modules and 'PySide' not in sys.modules\nprint(json.dumps(rows))`;
  const result=spawnSync('python3',['-I','-B','-c',script,modulePath],{encoding:'utf8',maxBuffer:256*1024});
  assert.equal(result.status,0,result.stderr);
  for(const value of JSON.parse(result.stdout)) assert.equal(PftDrawingExportDataSchema.safeParse(value).success,true,value.error.code);
});

async function sdk(t: TestContext,scope: 'full'|'chat') {
  const calls:Array<{method:string;params:Record<string,unknown>}>=[];
  const state:{cap:unknown;value:any}={cap:PFT_DRAWING_EXPORT_CAPABILITY,value:dto()};
  const bridge=httpServer(async(req,res)=>{let text='';for await(const chunk of req)text+=chunk;const body=JSON.parse(text);calls.push(body);res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({ok:true,data:body.method==='get_status'?{protocol_version:1,supported_methods:methods,drawing_export:state.cap}:state.value}));});
  await new Promise<void>(resolve=>bridge.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise<void>(resolve=>bridge.close(()=>resolve())));
  const address=bridge.address();assert(address&&typeof address!=='string');
  const server=createServer({freecadUrl:`http://127.0.0.1:${address.port}/`,timeoutMs:500,toolScope:scope});
  const [a,b]=InMemoryTransport.createLinkedPair();await server.connect(a);
  const client=new Client({name:'neutral-export-sdk',version:'0.3.2'});await client.connect(b);t.after(()=>client.close());
  return{client,calls,state};
}

test('full28/chat22正式SDK发现三工具，注解/输入/内存来源与单次派发准确', async t => {
  for (const scope of ['full','chat'] as const) {
    const {client,calls,state}=await sdk(t,scope);const tools=(await client.listTools()).tools;
    assert.equal(tools.length,scope==='full'?28:22);
    for(const method of methods){const tool=tools.find(value=>value.name===method)!;assert(tool);assert.equal(tool.inputSchema.additionalProperties,false);assert.equal(tool.annotations?.idempotentHint,true);}
    assert.equal(tools.find(value=>value.name===methods[0])!.annotations?.readOnlyHint,true);
    assert.equal(tools.find(value=>value.name===methods[1])!.annotations?.readOnlyHint,false);
    const manifest=await client.callTool({name:'get_capabilities',arguments:{}});
    assert.deepEqual(manifest.structuredContent?.drawing_export,PFT_DRAWING_EXPORT_CAPABILITY);assert.equal(calls.length,0);
    const prepared=await client.callTool({name:methods[0],arguments:args});assert.equal(prepared.isError,undefined);
    assert.equal(prepared.structuredContent?.source_kind,'bridge_export_memory');assert.deepEqual(prepared.structuredContent?.data,state.value);
    for(const method of methods.slice(1)){const result=await client.callTool({name:method,arguments:{token}});assert.equal(result.isError,undefined);assert.equal(result.structuredContent?.source_kind,'bridge_export_memory');}
    assert.equal(calls.filter(value=>value.method!=='get_status').length,3);
    const before=calls.length;
    for(const method of methods){const result=await client.callTool({name:method,arguments:{...(method===methods[0]?args:{token}),confirmed:true}});assert.equal(result.isError,true);}
    assert.equal(calls.length,before);
  }
});

test('发现路由中export requestId和token始终绑定原service，显式换实例也不能迁移', async t => {
  const directory=realpathSync(mkdtempSync(join(process.platform==='darwin'?'/private/tmp':tmpdir(),'pft-export-routing-')));
  chmodSync(directory,0o700);t.after(()=>rmSync(directory,{recursive:true,force:true}));
  async function endpoint(id: string,service: string) {
    const calls:string[]=[];
    const server=httpServer(async(req,res)=>{let text='';for await(const chunk of req)text+=chunk;const body=JSON.parse(text);calls.push(body.method);
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,data:body.method==='get_status'?{
        protocol_version:1,addon_version:VERSION,supported_methods:methods,drawing_export:PFT_DRAWING_EXPORT_CAPABILITY,
        instance_routing:{version:1,instance_id:id,service_id:service,pid:123,guard_required:true}}:dto()}));});
    await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise<void>(resolve=>server.close(()=>resolve())));
    const address=server.address();assert(address&&typeof address!=='string');
    return{calls,record:{schema_version:1,owner:'phoenix-freecad-tools',instance_id:id,service_id:service,pid:123,
      url:`http://127.0.0.1:${address.port}/`,protocol_version:1,addon_version:VERSION}};
  }
  const a=await endpoint('a'.repeat(32),'c'.repeat(32));
  const publish=(value:typeof a.record)=>writeFileSync(join(directory,value.instance_id+'.json'),JSON.stringify(value),{mode:0o600,flag:'wx'});
  publish(a.record);
  const server=createServer({freecadUrl:'http://127.0.0.1:1/',timeoutMs:500,instanceDiscovery:{runtimeDirectory:directory}});
  const [left,right]=InMemoryTransport.createLinkedPair();await server.connect(left);
  const client=new Client({name:'neutral-export-affinity',version:'0.3.2'});await client.connect(right);t.after(()=>client.close());
  assert.equal((await client.callTool({name:'prepare_drawing_export',arguments:args})).isError,undefined);
  const b=await endpoint('b'.repeat(32),'d'.repeat(32));publish(b.record);
  assert.equal((await client.callTool({name:'select_freecad_instance',arguments:{instanceId:b.record.instance_id,serviceId:b.record.service_id}})).isError,undefined);
  for(const method of methods){const result=await client.callTool({name:method,arguments:method===methods[0]?args:{token}});
    assert.equal(result.isError,true);assert.equal((result.structuredContent?.error as any)?.code,'INSTANCE_AFFINITY_MISMATCH');}
  assert.equal(a.calls.filter(method=>method!=='get_status').length,1);
  assert.equal(b.calls.filter(method=>method!=='get_status').length,0);
});

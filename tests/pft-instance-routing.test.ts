// SPDX-License-Identifier: Apache-2.0
/** Synthetic private registries and loopback services only; no user config or CAD. */
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, linkSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createServer as httpServer } from 'node:http';
import { Client, InMemoryTransport } from '@modelcontextprotocol/client';
import { pftReadInstanceRegistry, pftResolveInstanceDirectory } from '../src/pft-instance-registry.js';
import { PftInstanceRouter } from '../src/pft-instance-router.js';
import { PftFreeCadClient } from '../src/adapters/pft-freecad-client.js';
import { createServer, TOOL_NAMES, CHAT_TOOL_NAMES } from '../src/server.js';
const A='a'.repeat(32), B='b'.repeat(32), S='c'.repeat(32), T='d'.repeat(32);
function fixture(t: TestContext) {
  const directory=realpathSync(mkdtempSync(join(process.platform==='darwin'?'/private/tmp':tmpdir(),'pft-instance-test-')));chmodSync(directory,0o700);
  t.after(()=>rmSync(directory,{recursive:true,force:true}));return directory;
}
function record(instance_id=A,service_id=S,url='http://127.0.0.1:9988/') {
  return {schema_version:1,owner:'phoenix-freecad-tools',instance_id,service_id,pid:123,url,protocol_version:1,addon_version:'0.4.1'};
}
function publish(directory:string,value=record(),raw?:string) {
  const path=join(directory,value.instance_id+'.json');writeFileSync(path,raw??JSON.stringify(value),{mode:0o600});chmodSync(path,0o600);return path;
}
async function bridge(t:TestContext,id=A,service=S) {
  const calls:Array<{method:string;params:Record<string,unknown>;target?:unknown}>=[];
  let seenDocument!:()=>void;const documentStarted=new Promise<void>(resolve=>{seenDocument=resolve;});
  const state={service,guard:true,guardStatus:false,routing:true,hold:false,addon:'0.4.1',release:()=>{}, statusId:id};
  const server=httpServer(async(req,res)=>{
    let text='';for await(const chunk of req)text+=chunk;const body=JSON.parse(text);calls.push(body);
    res.setHeader('Content-Type','application/json');
    if((body.method!=='get_status' || state.guardStatus && body.target!==undefined) && state.guard && JSON.stringify(body.target)!==JSON.stringify({instance_id:state.statusId,service_id:state.service})) {
      res.end(JSON.stringify({ok:false,error:{code:'INSTANCE_TARGET_MISMATCH',message:'Synthetic guard rejected'}}));return;
    }
    if(body.method==='list_documents')seenDocument();
    if(body.method==='list_documents' && state.hold)await new Promise<void>(resolve=>{state.release=resolve;});
    const data=body.method==='get_status'?{protocol_version:1,addon_version:state.addon,supported_methods:['list_documents','execute_script','get_script_status','prepare_part_changes'],
      ...(state.routing?{instance_routing:{version:1,instance_id:state.statusId,service_id:state.service,pid:123,guard_required:state.guard}}:{})}
      :body.method==='list_documents'?[id]:body.method==='prepare_part_changes'?{token:'fixture_owned_preview_token'}:{request_id:body.params.requestId,status:'completed',result:{fixture:true}};
    res.end(JSON.stringify({ok:true,data}));
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{state.release();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));});
  const port=(server.address() as {port:number}).port;
  return {url:`http://127.0.0.1:${port}/`,calls,state,documentStarted};
}
async function mcp(t:TestContext,config:Parameters<typeof createServer>[0]) {
  const server=createServer(config),client=new Client({name:'pft-instance-test',version:'1'});
  const [left,right]=InMemoryTransport.createLinkedPair();await Promise.all([server.connect(left),client.connect(right)]);
  t.after(async()=>{await client.close();await server.close();});return client;
}
const data=(value:unknown)=>value as {structuredContent:{ok:boolean;data:unknown;error?:{code:string}}};

test('registry missing directory stays missing; resolver matches fixed platform suffixes without writes',t=>{
  const home=fixture(t),missing=join(home,'absent');assert.deepEqual(pftReadInstanceRegistry({directory:missing}),[]);assert.equal(existsSync(missing),false);
  assert.equal(pftResolveInstanceDirectory({home,platform:'darwin',env:{}}),join(home,'Library/Application Support/PhoenixTools/mcp/runtime/instances'));
  assert.equal(pftResolveInstanceDirectory({home,platform:'linux',env:{}}),join(home,'.local/state/PhoenixTools/mcp/runtime/instances'));
  assert.equal(pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_RUNTIME_DIR:missing}}),join(home,'.local/state/PhoenixTools/mcp/runtime/instances'));
  assert.equal(pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_RUNTIME_DIR:home}}),join(home,'phoenix-tools/mcp/instances'));
  assert.throws(()=>pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_RUNTIME_DIR:'relative'}}),{code:'INSTANCE_REGISTRY_INVALID'});
  assert.throws(()=>pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_STATE_HOME:'relative'}}),{code:'INSTANCE_REGISTRY_INVALID'});
  assert.throws(()=>pftResolveInstanceDirectory({home,platform:'win32',env:{}}),{code:'INSTANCE_PLATFORM_UNSUPPORTED'});
  chmodSync(home,0o755);assert.throws(()=>pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_RUNTIME_DIR:home}}),{code:'INSTANCE_REGISTRY_INVALID'});
});
test('registry yields frozen exact records/fingerprints and recognizes only private staging files',t=>{
  const dir=fixture(t),path=publish(dir),bytes=readFileSync(path);
  writeFileSync(join(dir,'.pft-instance-'+B+'.tmp'),'staging is not parsed',{mode:0o600});
  const rows=pftReadInstanceRegistry({directory:dir});assert.equal(rows.length,1);assert.ok(Object.isFrozen(rows));assert.ok(Object.isFrozen(rows[0]));
  assert.match(rows[0]!.fingerprint,/^[0-9a-f]{64}$/u);assert.equal(rows[0]!.url,record().url);assert.deepEqual(readFileSync(path),bytes);
  assert.equal(pftReadInstanceRegistry({directory:dir})[0]!.fingerprint,rows[0]!.fingerprint);
});
test('registry rejects duplicate decoded fields, float integer literals and arbitrary fields without fallback',t=>{
  const dir=fixture(t),base=JSON.stringify(record());
  const bad=[base.replace('{','{"\\u0075rl":"http://127.0.0.1:9988/",'),base.replace('"pid":123','"pid":1.23e2'),base.replace('"schema_version":1','"schema_version":1.0'),
    base.replace('"pid":123','"pid":true'),base.replace('"pid":123','"pid":9007199254740992'),base.replace('"url":"http://127.0.0.1:9988/"','"url":"http://localhost:9988/"'),
    base.replace('"owner":"phoenix-freecad-tools"','"owner":"other"'),base.replace('"addon_version":"0.4.1"','"addon_version":"9.0.0"'),
    base.replace('"addon_version":"0.4.1"','"addon_version":"0.3.2"'),base.replace('"addon_version":"0.4.1"','"addon_version":"0.4.0"'),base.replace('}',',"private":"not accepted"}'),'{"nested":{}}'];
  for(const raw of bad){publish(dir,record(),raw);assert.throws(()=>pftReadInstanceRegistry({directory:dir}),{code:'INSTANCE_REGISTRY_INVALID'});}
});
test('registry refuses symlinks hardlinks nonprivate modes unknown entries oversized files and bounded counts',t=>{
  const dir=fixture(t),path=publish(dir);
  chmodSync(path,0o644);assert.throws(()=>pftReadInstanceRegistry({directory:dir}));chmodSync(path,0o600);
  linkSync(path,join(dir,'other'));assert.throws(()=>pftReadInstanceRegistry({directory:dir}));rmSync(join(dir,'other'));
  const alternate=join(dir,'saved');renameSync(path,alternate);symlinkSync(alternate,path);assert.throws(()=>pftReadInstanceRegistry({directory:dir}));rmSync(path);renameSync(alternate,path);
  publish(dir,record(),'x'.repeat(8193));assert.throws(()=>pftReadInstanceRegistry({directory:dir}));publish(dir);
  writeFileSync(join(dir,'unknown.json'),'{}',{mode:0o600});assert.throws(()=>pftReadInstanceRegistry({directory:dir}));rmSync(join(dir,'unknown.json'));
  for(let i=0;i<201;i++)writeFileSync(join(dir,'.pft-instance-'+i.toString(16).padStart(32,'0')+'.tmp'),'',{mode:0o600});
  assert.throws(()=>pftReadInstanceRegistry({directory:dir}),{code:'INSTANCE_REGISTRY_LIMIT'});
});
test('direct client pins routing on first status, guards actions and refuses a restarted service',async t=>{
  const b=await bridge(t),client=new PftFreeCadClient({url:b.url,timeoutMs:300});
  await client.listDocuments();assert.equal(b.calls[0]!.target,undefined);assert.deepEqual(b.calls.at(-1)!.target,{instance_id:A,service_id:S});
  const before=b.calls.filter(c=>c.method==='list_documents').length;b.state.service=T;
  await assert.rejects(client.listDocuments(),{code:'INSTANCE_IDENTITY_MISMATCH'});assert.equal(b.calls.filter(c=>c.method==='list_documents').length,before);
});
test('record client requires exact IDs and PID even on initial guarded status; legacy client remains supported',async t=>{
  const b=await bridge(t);const client=new PftFreeCadClient({url:b.url,timeoutMs:300,target:{instance_id:A,service_id:S,pid:999}});
  await assert.rejects(client.status(),{code:'INSTANCE_IDENTITY_MISMATCH'});assert.deepEqual(b.calls[0]!.target,{instance_id:A,service_id:S});
  b.state.routing=false;b.state.guard=false;await new PftFreeCadClient({url:b.url,timeoutMs:300}).listDocuments();
});
test('router initially binds one ready target; lost binding never adopts the remaining singleton',async t=>{
  const dir=fixture(t),a=await bridge(t),b=await bridge(t,B,T);publish(dir,record(A,S,a.url));const router=new PftInstanceRouter({runtimeDirectory:dir,timeoutMs:300});
  const lease=await router.acquire();assert.deepEqual(await lease.client.listDocuments(),[A]);lease.release();publish(dir,record(B,T,b.url));rmSync(join(dir,A+'.json'));
  await assert.rejects(router.acquire(),{code:'INSTANCE_TARGET_CHANGED'});assert.equal(b.calls.length,0);
  await router.select(B,T);const next=await router.acquire();assert.deepEqual(await next.client.listDocuments(),[B]);next.release();
});
test('router requires explicit multiple-instance selection and preserves in-flight client and affinities',async t=>{
  const dir=fixture(t),a=await bridge(t),b=await bridge(t,B,T);publish(dir,record(A,S,a.url));publish(dir,record(B,T,b.url));
  const router=new PftInstanceRouter({runtimeDirectory:dir,timeoutMs:300});await assert.rejects(router.acquire(),{code:'INSTANCE_SELECTION_REQUIRED'});
  await router.select(A,S);const first=await router.acquire();first.bindRequest('neutral-request');first.bindToken('neutral-preview-token');
  await assert.rejects(router.select(B,T),{code:'INSTANCE_BUSY'});assert.deepEqual(await first.client.listDocuments(),[A]);first.release();
  await router.select(B,T);const second=await router.acquire();assert.throws(()=>second.bindRequest('neutral-request'),{code:'INSTANCE_AFFINITY_MISMATCH'});
  assert.throws(()=>second.bindToken('neutral-preview-token'),{code:'INSTANCE_AFFINITY_MISMATCH'});second.bindRequest('new-request');second.release();
  await router.select(A,S);const third=await router.acquire();third.bindRequest('neutral-request');third.release();
});
test('router rejects same-ID record replacement after binding and enforces non-evicting affinity limits',async t=>{
  const dir=fixture(t),a=await bridge(t);publish(dir,record(A,S,a.url));const router=new PftInstanceRouter({runtimeDirectory:dir,timeoutMs:300});
  const lease=await router.acquire();for(let i=0;i<4096;i++)lease.bindRequest('request-'+i);assert.throws(()=>lease.bindRequest('overflow'),{code:'INSTANCE_AFFINITY_LIMIT'});lease.bindRequest('request-0');lease.release();
  publish(dir,record(A,S,a.url),JSON.stringify(record(A,S,a.url),null,2));await assert.rejects(router.acquire(),{code:'INSTANCE_TARGET_CHANGED'});
});
test('router lists only exact registered endpoints and classifies incompatible and identity mismatches',async t=>{
  const dir=fixture(t),a=await bridge(t),b=await bridge(t,B,T);publish(dir,record(A,S,a.url));publish(dir,record(B,T,b.url));a.state.addon='other';b.state.statusId=A;b.state.guardStatus=true;
  const rows=await new PftInstanceRouter({runtimeDirectory:dir,timeoutMs:300}).list();assert.deepEqual(rows.map(r=>r.state),['incompatible','identity_mismatch']);
  const bRequest=b.calls[0]!;assert.deepEqual(bRequest.target,{instance_id:B,service_id:T});
  assert.ok(a.calls.every(c=>c.method==='get_status'));assert.ok(b.calls.every(c=>c.method==='get_status'));
});
test('MCP discovery advertises 30 tools offline, requires selection, routes scripts and blocks affinity migration',async t=>{
  const dir=fixture(t),a=await bridge(t),b=await bridge(t,B,T);publish(dir,record(A,S,a.url));publish(dir,record(B,T,b.url));
  const client=await mcp(t,{freecadUrl:'http://127.0.0.1:1/',timeoutMs:300,instanceDiscovery:{runtimeDirectory:dir}});
  const tools=await client.listTools();assert.equal(tools.tools.length,30);assert.ok(tools.tools.some(v=>v.name==='select_freecad_instance'));
  const capabilities=data(await client.callTool({name:'get_capabilities',arguments:{}}));assert.equal(a.calls.length+b.calls.length,0);assert.equal(capabilities.structuredContent.ok,true);
  const ambiguous=data(await client.callTool({name:'list_documents',arguments:{}}));assert.equal(ambiguous.structuredContent.error?.code,'INSTANCE_SELECTION_REQUIRED');
  await client.callTool({name:'select_freecad_instance',arguments:{instanceId:A,serviceId:S}});
  const result=data(await client.callTool({name:'execute_script',arguments:{requestId:'fixture-request',code:'print(1)'}}));assert.equal(result.structuredContent.ok,true);
  await client.callTool({name:'select_freecad_instance',arguments:{instanceId:B,serviceId:T}});
  const mismatch=data(await client.callTool({name:'get_script_status',arguments:{requestId:'fixture-request'}}));assert.equal(mismatch.structuredContent.error?.code,'INSTANCE_AFFINITY_MISMATCH');
  assert.equal(b.calls.filter(c=>c.method==='get_script_status').length,0);
});
test('MCP legacy surfaces stay 28/22/4 and programmatic chat/worker discovery is rejected',async t=>{
  for(const scope of ['full','chat','worker'] as const){const client=await mcp(t,{freecadUrl:'http://127.0.0.1:1/',timeoutMs:100,toolScope:scope});const list=await client.listTools();assert.equal(list.tools.length,scope==='full'?28:scope==='chat'?22:4);}
  for(const toolScope of ['chat','worker'] as const)assert.throws(()=>createServer({freecadUrl:'http://127.0.0.1:1/',timeoutMs:100,toolScope,instanceDiscovery:{runtimeDirectory:'/not-read'}}),{code:'INSTANCE_SCOPE_UNSUPPORTED'});
  assert.equal(TOOL_NAMES.length,28);assert.equal(CHAT_TOOL_NAMES.length,22);
});


test('MCP rejects concurrent selection while an awaited operation retains its original bound client',{timeout:3000},async t=>{
  const dir=fixture(t),a=await bridge(t),b=await bridge(t,B,T);publish(dir,record(A,S,a.url));publish(dir,record(B,T,b.url));
  const client=await mcp(t,{freecadUrl:'http://127.0.0.1:1/',timeoutMs:1000,instanceDiscovery:{runtimeDirectory:dir}});
  await client.callTool({name:'select_freecad_instance',arguments:{instanceId:A,serviceId:S}});a.state.hold=true;
  const pending=client.callTool({name:'list_documents',arguments:{}});await a.documentStarted;
  const busy=data(await client.callTool({name:'select_freecad_instance',arguments:{instanceId:B,serviceId:T}}));assert.equal(busy.structuredContent.error?.code,'INSTANCE_BUSY');
  a.state.release();const returned=data(await pending);assert.deepEqual(returned.structuredContent.data,[A]);
  assert.equal(b.calls.filter(c=>c.method==='list_documents').length,0);
  await client.callTool({name:'select_freecad_instance',arguments:{instanceId:B,serviceId:T}});
  assert.deepEqual(data(await client.callTool({name:'list_documents',arguments:{}})).structuredContent.data,[B]);
});
test('same instance listener restart needs reselect and old request/token affinities never migrate',async t=>{
  const dir=fixture(t),a=await bridge(t);publish(dir,record(A,S,a.url));const router=new PftInstanceRouter({runtimeDirectory:dir,timeoutMs:300});
  const old=await router.acquire();old.bindRequest('original-request');old.bindToken('original-token');old.release();a.state.service=T;publish(dir,record(A,T,a.url));
  await assert.rejects(router.acquire(),{code:'INSTANCE_TARGET_CHANGED'});await router.select(A,T);const next=await router.acquire();
  assert.throws(()=>next.bindRequest('original-request'),{code:'INSTANCE_AFFINITY_MISMATCH'});assert.throws(()=>next.bindToken('original-token'),{code:'INSTANCE_AFFINITY_MISMATCH'});next.release();
});
test('MCP prepares bind returned tokens before explicit selection can change the target',async t=>{
  const dir=fixture(t),a=await bridge(t),b=await bridge(t,B,T);publish(dir,record(A,S,a.url));publish(dir,record(B,T,b.url));
  const client=await mcp(t,{freecadUrl:'http://127.0.0.1:1/',timeoutMs:300,instanceDiscovery:{runtimeDirectory:dir}});
  await client.callTool({name:'select_freecad_instance',arguments:{instanceId:A,serviceId:S}});
  const preview=data(await client.callTool({name:'prepare_part_changes',arguments:{document:'PublicFixture',objects:['Body'],operation:'normalize_names'}}));assert.equal(preview.structuredContent.ok,true);
  await client.callTool({name:'select_freecad_instance',arguments:{instanceId:B,serviceId:T}});
  const mismatch=data(await client.callTool({name:'apply_part_changes',arguments:{token:'fixture_owned_preview_token'}}));assert.equal(mismatch.structuredContent.error?.code,'INSTANCE_AFFINITY_MISMATCH');
  assert.equal(b.calls.filter(c=>c.method==='apply_part_changes').length,0);
});


test('resolver and registry refuse symlink ancestors and do not follow unsafe existing runtime roots',t=>{
  const home=fixture(t),real=join(home,'real');mkdirSync(real,{mode:0o700});const linked=join(home,'linked');symlinkSync(real,linked);
  assert.throws(()=>pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_RUNTIME_DIR:linked}}),{code:'INSTANCE_REGISTRY_INVALID'});
  assert.throws(()=>pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_RUNTIME_DIR:join(linked,'missing')}}),{code:'INSTANCE_REGISTRY_INVALID'});
  assert.throws(()=>pftReadInstanceRegistry({directory:join(linked,'missing')}),{code:'INSTANCE_REGISTRY_INVALID'});
  assert.throws(()=>pftReadInstanceRegistry({directory:home+'/./'}),{code:'INSTANCE_REGISTRY_INVALID'});
});
test('full discovery plus optional Desk advertises 35 tools without registry or Desk IO',async t=>{
  const client=await mcp(t,{freecadUrl:'http://127.0.0.1:1/',timeoutMs:100,instanceDiscovery:{runtimeDirectory:'/must-not-be-read'},
    desk:{nodeExecutable:'/fixture/node',cliEntry:'/fixture/desk.mjs',stateDir:'/fixture/state'}});
  assert.equal((await client.listTools()).tools.length,35);const manifest=data(await client.callTool({name:'get_capabilities',arguments:{}}));assert.equal(manifest.structuredContent.ok,true);
});


test('identities and staging names require exact final boundaries, not a pre-newline dollar match',t=>{
  const dir=fixture(t);const original=record();publish(dir,{...original,service_id:S+'\n'});assert.throws(()=>pftReadInstanceRegistry({directory:dir}));publish(dir);
  writeFileSync(join(dir,'.pft-instance-'+B+'.tmp\n'),'',{mode:0o600});assert.throws(()=>pftReadInstanceRegistry({directory:dir}));
  assert.throws(()=>new PftFreeCadClient({url:original.url,timeoutMs:100,target:{instance_id:A,service_id:S+'\n'}}),{code:'INSTANCE_IDENTITY_MISMATCH'});
  for(const path of [dir+' ', '//ambiguous/path', '/'+ '名'.repeat(1400)])assert.throws(()=>pftReadInstanceRegistry({directory:path}));
});


test('transient symlink ancestor replacement restored before final checks is rejected before any handshake',async t=>{
  const root=fixture(t),parent=join(root,'parent'),alternate=join(root,'alternate'),backup=join(root,'backup');
  mkdirSync(parent,{mode:0o700});mkdirSync(alternate,{mode:0o700});mkdirSync(join(parent,'instances'),{mode:0o700});mkdirSync(join(alternate,'instances'),{mode:0o700});
  const directory=join(parent,'instances'),file=publish(directory);publish(join(alternate,'instances'),record(A,S,'http://127.0.0.1:9989/'));
  const original=fs.lstatSync;let active=false,count=0;
  try {
    fs.lstatSync=((path:fs.PathLike,...args:unknown[])=>{
      if(path===file && ++count===1){renameSync(parent,backup);symlinkSync(alternate,parent);active=true;}
      const value=Reflect.apply(original,fs,[path,...args]);
      if(path===file && count===2){unlinkSync(parent);renameSync(backup,parent);active=false;}
      return value;
    }) as typeof fs.lstatSync;
    syncBuiltinESMExports();
    await assert.rejects(new PftInstanceRouter({runtimeDirectory:directory,timeoutMs:100}).list(),{code:'INSTANCE_REGISTRY_INVALID'});
  } finally {
    fs.lstatSync=original;syncBuiltinESMExports();if(active){unlinkSync(parent);renameSync(backup,parent);}
  }
  assert.equal(JSON.parse(readFileSync(file,'utf8')).url,record().url);
});


test('fixed Linux runtime root stays private after resolution while unrelated same-named ancestors stay ordinary',t=>{
  const home=fixture(t),runtime=join(home,'runtime');mkdirSync(runtime,{mode:0o700});const directory=pftResolveInstanceDirectory({home,platform:'linux',env:{XDG_RUNTIME_DIR:runtime}});
  mkdirSync(directory,{recursive:true,mode:0o700});publish(directory);assert.equal(pftReadInstanceRegistry({directory}).length,1);
  chmodSync(runtime,0o755);assert.throws(()=>pftReadInstanceRegistry({directory}));
  const named=join(home,'PhoenixTools');mkdirSync(named,{mode:0o755});const custom=join(named,'custom');mkdirSync(custom,{mode:0o700});publish(custom);assert.equal(pftReadInstanceRegistry({directory:custom}).length,1);
});

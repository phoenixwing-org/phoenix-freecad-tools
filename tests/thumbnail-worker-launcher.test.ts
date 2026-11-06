// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {prepareDevelopment} from '../scripts/dev-freecad.mjs';
import {prepareThumbnailWorker} from '../scripts/thumbnail-worker.mjs';

function fixture(t: {after(callback: () => void): void}) {
 const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'pft-worker-launch-')));
 t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const repository=path.join(root,'source'),profile=path.join(root,'worker');
 const source=path.join(repository,'addon','PhoenixFreeCADTools');
 fs.mkdirSync(source,{recursive:true});fs.writeFileSync(path.join(source,'InitGui.py'),'# independent fixture\n');
 const app=path.join(root,'synthetic executable with spaces');
 fs.writeFileSync(app,'# never executed\n',{mode:0o700});
 return {root,repository,profile,app};
}

test('worker requires explicit profile and rejects unsupported native platforms',t=>{
 const f=fixture(t);
 assert.throws(()=>prepareThumbnailWorker({app:f.app,repository:f.repository}));
 assert.equal(fs.existsSync(f.profile),false);
 if(process.platform!=='darwin')assert.throws(()=>prepareThumbnailWorker(f),/macOS/);
});
test('worker prepare stays separate and returns data argv plus unique readiness identity',{skip:process.platform!=='darwin'},t=>{
 const f=fixture(t);const beforeHome=process.env.HOME;
 const prepared=prepareThumbnailWorker(f);
 assert.equal(prepared.launch.launched,false);
 assert.equal(prepared.launch.shell,false);
 assert.equal(prepared.launch.args.at(-1),path.join(f.profile,'thumbnail-worker.FCMacro'));
 assert.equal(prepared.environment.HOME,beforeHome);
 assert.equal(process.env.HOME,beforeHome);
 assert.equal(prepared.launch.worker.port_policy,'os-assigned-loopback');
 assert.equal(prepared.environment.PFT_THUMBNAIL_WORKER_SESSION,prepared.launch.worker.session_id);
 const marker=JSON.parse(fs.readFileSync(path.join(f.profile,'.phoenix-thumbnail-worker.json'),'utf8'));
 assert.equal(marker.worker_id,prepared.launch.worker.worker_id);
 const launch=JSON.parse(fs.readFileSync(prepared.launch.worker_launch_record,'utf8'));
 assert.deepEqual(launch.args,prepared.launch.args);
 assert.deepEqual(launch.worker,prepared.launch.worker);
 assert.equal(fs.existsSync(prepared.launch.worker.readiness_receipt),false);
 assert.equal(fs.existsSync(prepared.launch.process_record),false);
 const macro=fs.readFileSync(prepared.launch.args.at(-1),'utf8');
 assert.match(macro,/pft_thumbnail_worker.bootstrap/);
 assert.doesNotMatch(macro,/openDocument|saveAs|saveDocument|source\/|synthetic executable/);
 const next=prepareThumbnailWorker(f);
 assert.equal(next.launch.worker.worker_id,prepared.launch.worker.worker_id);
 assert.notEqual(next.launch.worker.session_id,prepared.launch.worker.session_id);
});
test('ordinary development profile is not silently converted',{skip:process.platform!=='darwin'},t=>{
 const f=fixture(t);prepareDevelopment(f);
 const files=fs.readdirSync(f.profile).sort();
 assert.throws(()=>prepareThumbnailWorker(f));
 assert.deepEqual(fs.readdirSync(f.profile).sort(),files);
 assert.equal(fs.existsSync(path.join(f.profile,'.phoenix-thumbnail-worker.json')),false);
});
test('unknown or edited worker metadata is preserved',{skip:process.platform!=='darwin'},t=>{
 const f=fixture(t);prepareThumbnailWorker(f);
 const marker=path.join(f.profile,'.phoenix-thumbnail-worker.json');
 fs.writeFileSync(marker,'{"owner":"unknown"}\n');
 assert.throws(()=>prepareThumbnailWorker(f));
 assert.equal(fs.readFileSync(marker,'utf8'),'{"owner":"unknown"}\n');
});

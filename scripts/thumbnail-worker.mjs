// SPDX-License-Identifier: Apache-2.0
// Explicit macOS development worker; no default FreeCAD profile is adopted.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {parseArgs} from 'node:util';
import {fileURLToPath} from 'node:url';
import {prepareDevelopment, launchDevelopment} from './dev-freecad.mjs';

const MARKER='.phoenix-thumbnail-worker.json';
const MACRO=`# Owned thumbnail worker startup; no user document is opened at startup.\nimport FreeCAD as App\nimport FreeCADGui as Gui\nimport pft_development\nimport pft_thumbnail_worker\npft_development.write_readiness(App, Gui)\npft_thumbnail_worker.bootstrap(App, Gui)\n`;
function checkedOwnedFile(filename, maximum=8192) {
 const info=fs.lstatSync(filename);
 if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||info.uid!==process.getuid()||(info.mode&0o077)||info.size>maximum)throw new Error('Worker metadata is not a private owned regular file.');
 return fs.readFileSync(filename,'utf8');
}
export function prepareThumbnailWorker({profile,app,repository}={}) {
 if(process.platform!=='darwin')throw new Error('The owned thumbnail launcher currently has native validation only on macOS.');
 if(typeof profile!=='string'||!path.isAbsolute(profile))throw new Error('Choose an explicit absolute private worker profile outside source and projects.');
 const root=path.resolve(profile), fresh=!fs.existsSync(root);
 // Refuse converting even an idle ordinary development profile into a worker.
 let previous;
 if(!fresh){
  const info=fs.lstatSync(root);
  if(!info.isDirectory()||info.isSymbolicLink()||info.uid!==process.getuid()||(info.mode&0o777)!==0o700||fs.realpathSync(root)!==root)throw new Error('Worker profile is not a real private owned directory.');
  previous=JSON.parse(checkedOwnedFile(path.join(root,MARKER),4096));
 }
 const prepared=prepareDevelopment({profile:root,...(app?{app}:{}),...(repository?{repository}:{})});
 const identity={schema_version:1,owner:'phoenix-thumbnail-worker',development_owner_id:prepared.launch.owner_id,
   source:prepared.launch.source,worker_id:fresh?randomUUID():previous.worker_id};
 if(!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(identity.worker_id)
   ||!fresh&&JSON.stringify(previous)!==JSON.stringify(identity))throw new Error('Worker profile identity changed; it was not adopted.');
 if(fresh)fs.writeFileSync(path.join(root,MARKER),JSON.stringify(identity)+'\n',{flag:'wx',mode:0o600});
 const macro=path.join(root,'thumbnail-worker.FCMacro');
 if(fresh)fs.writeFileSync(macro,MACRO,{flag:'wx',mode:0o600});
 else if(checkedOwnedFile(macro)!==MACRO)throw new Error('Worker startup macro changed; it was not replaced.');
 const session=randomUUID();
 prepared.environment.PFT_THUMBNAIL_WORKER_ID=identity.worker_id;
 prepared.environment.PFT_THUMBNAIL_WORKER_SESSION=session;
 prepared.launch.args=[...prepared.launch.args.slice(0,-1),macro];
 prepared.launch.worker={schema_version:1,worker_id:identity.worker_id,session_id:session,
   readiness_receipt:path.join(root,'runtime','thumbnail-worker-'+session+'.json'),
   profile_scope:'owned-development-worker',port_policy:'os-assigned-loopback',saves_cad:false};
 const record=path.join(root,'runtime','thumbnail-launch-'+session+'.json');
 prepared.launch.worker_launch_record=record;
 fs.writeFileSync(record,JSON.stringify(prepared.launch,null,2)+'\n',{flag:'wx',mode:0o600});
 return prepared;
}
async function main() {
 const {values}=parseArgs({options:{profile:{type:'string'},app:{type:'string'},repository:{type:'string'},prepare:{type:'boolean',default:false},help:{type:'boolean',default:false}},strict:true,allowPositionals:false});
 if(values.help){process.stdout.write('Usage: node scripts/thumbnail-worker.mjs --profile ABSOLUTE_PRIVATE_DIR [--app FreeCAD.app] [--prepare]\nExplicitly launches one owned macOS GUI worker. It does not open input, install an Addon or use your default profile.\n');return;}
 const prepared=prepareThumbnailWorker(values);
 const result=values.prepare?prepared.launch:await launchDevelopment(prepared);
 process.stdout.write(JSON.stringify(result,null,2)+'\n');
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(()=>{process.stderr.write('Thumbnail worker preparation or launch failed. Check the explicit owned profile; no unknown process was stopped.\n');process.exitCode=1;});

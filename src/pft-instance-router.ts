// SPDX-License-Identifier: Apache-2.0
import { PftFreeCadClient, PftBridgeError, type PftInstanceTarget } from './adapters/pft-freecad-client.js';
import { PftInstanceError, pftReadInstanceRegistry, pftInstanceIdValid, type PftRegisteredInstance } from './pft-instance-registry.js';

export const PFT_INSTANCE_TOOL_NAMES = ['list_freecad_instances', 'select_freecad_instance'] as const;
export type PftInstanceListing = Readonly<{ instance_id: string; service_id: string; pid: number;
  display_name: string; state: 'ready' | 'unavailable' | 'incompatible' | 'identity_mismatch'; selected: boolean }>;
type Candidate = { record: PftRegisteredInstance; listing: PftInstanceListing };
export type PftInstanceLease = Readonly<{ client: PftFreeCadClient; identity: PftInstanceTarget;
  bindRequest(requestId: string): void; bindToken(token: string): void; release(): void }>;
function error(code: string, message: string): never { throw new PftBridgeError(code, message); }
/** Session-owned selection/affinities. A lease never changes its client across awaits. */
export class PftInstanceRouter {
  private selected: PftRegisteredInstance | undefined;
  private inFlight = 0;
  private selecting = false;
  private readonly requests = new Map<string,string>();
  private readonly tokens = new Map<string,string>();
  constructor(private readonly options: { runtimeDirectory: string; timeoutMs: number }) {}
  private records(): readonly PftRegisteredInstance[] {
    try { return pftReadInstanceRegistry({ directory: this.options.runtimeDirectory }); }
    catch (cause) { if (cause instanceof PftInstanceError) error(cause.code, cause.message); throw cause; }
  }
  private client(record: PftRegisteredInstance): PftFreeCadClient {
    return new PftFreeCadClient({ url: record.url, timeoutMs: Math.min(this.options.timeoutMs, 1500),
      target: { instance_id: record.instance_id, service_id: record.service_id, pid: record.pid } });
  }
  private async probe(record: PftRegisteredInstance): Promise<Candidate> {
    let state: PftInstanceListing['state'] = 'ready';
    try {
      const status = await this.client(record).status();
      if (status.addon_version !== record.addon_version) state = 'incompatible';
    } catch (cause) {
      state = cause instanceof PftBridgeError && ['INSTANCE_IDENTITY_MISMATCH','INSTANCE_TARGET_MISMATCH','INSTANCE_TARGET_REQUIRED'].includes(cause.code) ? 'identity_mismatch'
        : cause instanceof PftBridgeError && ['UNSUPPORTED_PROTOCOL','UNSUPPORTED_CAPABILITY','INVALID_RESPONSE'].includes(cause.code)
          ? 'incompatible' : 'unavailable';
    }
    return { record, listing: Object.freeze({ instance_id: record.instance_id, service_id: record.service_id, pid: record.pid,
      display_name: 'FreeCAD ' + record.instance_id.slice(0,8) + ' · PID ' + record.pid, state,
      selected: this.selected?.fingerprint === record.fingerprint }) };
  }
  private async candidates(): Promise<Candidate[]> {
    const records = this.records(), result: Candidate[] = new Array(records.length); let next = 0;
    const worker = async () => { for (;;) { const index = next++; const record = records[index]; if (!record) return; result[index] = await this.probe(record); } };
    await Promise.all(Array.from({ length: Math.min(4,records.length) }, worker)); return result;
  }
  async list(): Promise<readonly PftInstanceListing[]> { return Object.freeze((await this.candidates()).map(row => row.listing)); }
  async select(instanceId: string, serviceId: string): Promise<PftInstanceListing> {
    if (!pftInstanceIdValid(instanceId) || !pftInstanceIdValid(serviceId)) error('INSTANCE_INVALID_SELECTION','Select an exact listed FreeCAD instance and service identity.');
    if (this.inFlight || this.selecting) error('INSTANCE_BUSY','A FreeCAD invocation is in progress; selection was not changed.');
    this.selecting = true;
    try {
      const record = this.records().find(r => r.instance_id === instanceId && r.service_id === serviceId);
      if (!record) error('INSTANCE_NOT_FOUND','The selected FreeCAD registration is no longer present.');
      const candidate = await this.probe(record);
      if (candidate.listing.state !== 'ready') error('INSTANCE_NOT_READY','The selected FreeCAD identity is not ready; selection was not changed.');
      const current = this.records().find(r => r.fingerprint === record.fingerprint);
      if (!current) error('INSTANCE_TARGET_CHANGED','The selected FreeCAD registration changed; inspect and select again.');
      if (this.inFlight) error('INSTANCE_BUSY','A FreeCAD invocation is in progress; selection was not changed.');
      this.selected = record; return Object.freeze({ ...candidate.listing, selected: true });
    } finally { this.selecting = false; }
  }
  private affinity(map: Map<string,string>, key: string, record: PftRegisteredInstance): void {
    const binding = record.instance_id + ':' + record.service_id, previous = map.get(key);
    if (previous !== undefined && previous !== binding) error('INSTANCE_AFFINITY_MISMATCH','This request or preview belongs to another FreeCAD service; it was not moved or replayed.');
    if (previous === undefined && map.size >= 4096) error('INSTANCE_AFFINITY_LIMIT','The session affinity limit has been reached; no action was sent.');
    map.set(key,binding);
  }
  async acquire(): Promise<PftInstanceLease> {
    if (this.selecting) error('INSTANCE_BUSY','FreeCAD selection is being verified; no action was sent.');
    this.inFlight++; let released = false;
    const release = () => { if (!released) { released = true; this.inFlight--; } };
    try {
      if (this.selected === undefined) {
        const ready = (await this.candidates()).filter(r => r.listing.state === 'ready');
        if (ready.length === 0) error('INSTANCE_NOT_READY','No registered FreeCAD instance is ready; no action was sent.');
        if (ready.length !== 1) error('INSTANCE_SELECTION_REQUIRED','Multiple FreeCAD instances are ready; list and select an exact instance before acting.');
        if (this.selected === undefined) this.selected = ready[0]!.record;
      }
      const selected = this.selected;
      const record = this.records().find(r => r.fingerprint === selected.fingerprint);
      if (!record) error('INSTANCE_TARGET_CHANGED','The bound FreeCAD service changed or disappeared; explicit selection is required.');
      const client = new PftFreeCadClient({ url: record.url, timeoutMs: this.options.timeoutMs,
        target: { instance_id: record.instance_id, service_id: record.service_id, pid: record.pid } });
      const status = await client.status();
      if (status.addon_version !== record.addon_version) error('INSTANCE_INCOMPATIBLE','The bound FreeCAD addon is incompatible; no action was sent.');
      if (!this.records().some(r => r.fingerprint === record.fingerprint)) error('INSTANCE_TARGET_CHANGED','The bound FreeCAD registration changed during verification; no action was sent.');
      return Object.freeze({ client, identity: Object.freeze({ instance_id: record.instance_id, service_id: record.service_id, pid: record.pid }),
        bindRequest: (key: string) => this.affinity(this.requests,key,record),
        bindToken: (key: string) => this.affinity(this.tokens,key,record), release });
    } catch (cause) { release(); throw cause; }
  }
}

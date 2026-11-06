// SPDX-License-Identifier: Apache-2.0
import * as z from 'zod/v4';

export const PFT_MAX_READ_BYTES = 128 * 1024;
export const PFT_REVISION_SCOPE = 'observed_document_events_since_registration';
export const PftReadGuardFields = {
  expectedDocumentInstanceToken: z.string().regex(/^[A-Za-z0-9_-]{16,128}$/u).describe('上一次读取的文档实例 token；同名关闭重开后会失效').optional(),
  expectedRevision: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER).describe('上一次读取观察到的部分事件修订；必须同时提供实例 token，不是完整写入保护').optional(),
};
export type PftReadGuards = {
  expectedDocumentInstanceToken?: string | undefined;
  expectedRevision?: number | undefined;
};
export function pftRefineReadGuards(value: PftReadGuards, context: z.RefinementCtx): void {
  if (value.expectedRevision !== undefined && value.expectedDocumentInstanceToken === undefined) {
    context.addIssue({ code: 'custom', message: 'expectedRevision 必须同时提供 expectedDocumentInstanceToken', path: ['expectedRevision'] });
  }
}
export const PftReadGuardSchema = z.object(PftReadGuardFields).strict().superRefine(pftRefineReadGuards);
export const PftReadCapabilitySchema = z.object({
  version: z.literal(1), methods: z.array(z.string().regex(/^[a-z][a-z0-9_]{0,99}$/u)).max(128),
  revision_coverage: z.literal('partial'), write_guard: z.literal(false),
}).passthrough();
const provenance = z.object({ source: z.literal('live_native_document'), geometry_freshness: z.literal('not_verified'),
  recomputed: z.literal(false), write_guard: z.literal(false) }).strict();
const token = z.string().regex(/^[A-Za-z0-9_-]{32}$/u);
export const PftReadDataSchema = z.object({
  document_instance_token: token.nullable(), instance_token: token.nullable(),
  revision: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER).nullable(),
  revision_scope: z.enum([PFT_REVISION_SCOPE, 'unavailable']), revision_coverage: z.enum(['partial', 'unknown']),
  revision_status: z.enum(['observing', 'observer_unavailable', 'registration_failed', 'legacy_plugin']), provenance,
}).passthrough();
export const PftNativeReadDataSchema = PftReadDataSchema.extend({
  document: z.string().min(1).max(256).regex(/^[^\p{Cc}\p{Cs}]+$/u), source_kind: z.literal('live_freecad_gui'),
}).superRefine((value, context) => {
  if (value.document_instance_token === null || value.instance_token !== value.document_instance_token || value.revision_scope !== PFT_REVISION_SCOPE
    || value.revision_status === 'legacy_plugin') context.addIssue({ code: 'custom', message: '原生文档实例或来源无效' });
  if (value.revision_status === 'observing') {
    if (value.revision === null || value.revision_coverage !== 'partial') context.addIssue({ code: 'custom', message: '观察中的修订字段无效' });
  } else if (value.revision !== null || value.revision_coverage !== 'unknown') {
    context.addIssue({ code: 'custom', message: '不可观察时修订必须为 unknown' });
  }
});
export const PFT_LEGACY_READ_STATE = {
  document_instance_token: null, instance_token: null, revision: null, revision_scope: 'unavailable',
  revision_coverage: 'unknown', revision_status: 'legacy_plugin',
  provenance: { source: 'live_native_document', geometry_freshness: 'not_verified', recomputed: false, write_guard: false },
} as const;
/** Success-only: MCP error results deliberately bypass this schema in the SDK. */
export const PftReadSuccessSchema = z.object({ ok: z.literal(true), source_kind: z.literal('live_freecad_gui'),
  observed_at: z.string(), data: PftReadDataSchema }).strict();

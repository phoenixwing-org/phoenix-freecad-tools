// SPDX-License-Identifier: Apache-2.0
/** One bounded design-write input and capability gate. Preparing never grants confirmation. */
import * as z from 'zod/v4';
import { PftPartApprovalCapabilitySchema, PftPadNativeNameSchema } from './pft-part-approval.js';

export const PFT_PAD_LENGTH_CAPABILITY = Object.freeze({ version: 1, operation: 'set_pad_length',
  confirmation: 'native_gui_only', preview_method: 'prepare_part_changes', request_method: 'request_part_change_approval',
  status_method: 'get_part_change_status', length_unit: 'mm', minimum_mm: 0.01, maximum_mm: 10000,
  model_scope: 'single_document_first_pad', max_open_documents: 1, saves_document: false,
} as const);
export const PftPadLengthCapabilitySchema = z.object({ version: z.literal(1), operation: z.literal('set_pad_length'),
  confirmation: z.literal('native_gui_only'), preview_method: z.literal('prepare_part_changes'),
  request_method: z.literal('request_part_change_approval'), status_method: z.literal('get_part_change_status'),
  length_unit: z.literal('mm'), minimum_mm: z.literal(0.01), maximum_mm: z.literal(10000),
  model_scope: z.literal('single_document_first_pad'), max_open_documents: z.literal(1), saves_document: z.literal(false),
}).strict();
const name = z.string().min(1).max(256).regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$/u);
export const PftPadLengthInputSchema = z.object({ document: name, objects: z.array(PftPadNativeNameSchema).length(1),
  operation: z.literal('set_pad_length'), body: PftPadNativeNameSchema, lengthMm: z.number().finite().min(0.01).max(10000),
}).strict();
export type PftPadLengthInput = z.output<typeof PftPadLengthInputSchema>;
type PftPadLengthTransport = { status: () => Promise<Record<string, unknown>>;
  prepare: (params: Record<string, unknown>) => Promise<unknown> };
type PftPadLengthErrorConstructor = new (code: string, message: string) => Error;

/** Gate before dispatch; no old-plugin fallback, arbitrary property setter, apply call or retry. */
export async function pftPreparePadLength(input: PftPadLengthInput, transport: PftPadLengthTransport,
  ErrorClass: PftPadLengthErrorConstructor): Promise<unknown> {
  const parsed = PftPadLengthInputSchema.safeParse(input);
  if (!parsed.success) throw new ErrorClass('INVALID_ARGUMENT', 'Pad预览需要明确文档、Body、一个Pad及0.01至10000毫米的有限正Length；不接受外部确认或属性values。');
  const status = await transport.status();
  const required = ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'];
  const methods = status.supported_methods;
  if (!Array.isArray(methods) || required.some(method => !methods.includes(method))
    || status.pad_length_write === undefined || status.part_approvals === undefined) {
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前FreeCAD插件未声明完整的Pad Length预览及原生确认能力；未派发预览。');
  }
  if (!PftPadLengthCapabilitySchema.safeParse(status.pad_length_write).success
    || !PftPartApprovalCapabilitySchema.safeParse(status.part_approvals).success) {
    throw new ErrorClass('INVALID_RESPONSE', 'FreeCAD插件的Pad Length或原生确认能力声明格式无效；未派发预览。');
  }
  const value = parsed.data;
  return transport.prepare({ docName: value.document, objectNames: value.objects, operation: value.operation,
    bodyName: value.body, lengthMm: value.lengthMm });
}

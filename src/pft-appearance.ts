// SPDX-License-Identifier: Apache-2.0
import * as z from 'zod/v4';
import { PftBridgeError } from './adapters/pft-freecad-client.js';

const internalName = z.string().min(1).max(256).regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$/u);
const requestId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/u);
export const PftAppearanceInputSchema = z.object({
  document: internalName.describe('明确的当前活动文档内部 Name，不是显示 Label'),
  objects: z.array(internalName).min(1).max(100).refine(values => new Set(values).size === values.length, '对象名称不能重复'),
  operation: z.enum(['random_color', 'half_transparent', 'opaque', 'set_transparency']),
  transparency: z.number().int().min(0).max(100).describe('仅 set_transparency 必填：0完全不透明，100完全透明').optional(),
  requestId: requestId.describe('同一次外观请求的唯一 ID；超时后用同 ID 查询或重放，避免重复执行'),
}).strict().superRefine((value, context) => {
  if ((value.operation === 'set_transparency') !== (value.transparency !== undefined)) {
    context.addIssue({ code: 'custom', message: '仅 set_transparency 需要且允许 transparency 整数百分比', path: ['transparency'] });
  }
}).refine(value => Buffer.byteLength(JSON.stringify({ document: value.document, objects: value.objects, operation: value.operation, transparency: value.transparency }), 'utf8') <= 20 * 1024,
  '外观参数总 UTF-8 大小不能超过20 KiB');

export type PftAppearanceInput = z.infer<typeof PftAppearanceInputSchema>;

/** Fixed Python only. User-controlled text is UTF-8 JSON in one Base64 literal. */
export function pftAppearanceScript(input: PftAppearanceInput): string {
  const payload = Buffer.from(JSON.stringify({ document: input.document, objects: input.objects, operation: input.operation, transparency: input.transparency }), 'utf8').toString('base64');
  const code = `# Phoenix typed set_appearance
import base64 as _pft_base64
import importlib.util as _pft_importlib
import json as _pft_json
from pathlib import Path as _pft_Path
import sys as _pft_sys
import FreeCAD as _pft_App
import FreeCADGui as _pft_Gui
_pft_payload = _pft_json.loads(_pft_base64.b64decode("${payload}", validate=True).decode("utf-8"))
def _pft_resolve(filename):
    try:
        return _pft_Path(filename).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        raise RuntimeError("Known Phoenix addon module path is unavailable") from None
_pft_bridge = _pft_sys.modules.get("pft_bridge")
_pft_bridge_name = getattr(_pft_bridge, "__file__", None)
if not isinstance(_pft_bridge_name, str):
    raise RuntimeError("Known Phoenix addon bridge is unavailable")
_pft_bridge_file = _pft_resolve(_pft_bridge_name)
if _pft_bridge_file.name != "pft_bridge.py" or _pft_bridge_file.parent.name != "PhoenixFreeCADTools":
    raise RuntimeError("Known Phoenix addon bridge is unavailable")
_pft_expected = _pft_bridge_file.with_name("pft_visual_tools.py")
if _pft_expected.is_symlink() or not _pft_expected.is_file():
    raise RuntimeError("Known Phoenix visual addon module is unavailable")
_pft_expected = _pft_resolve(_pft_expected)
if _pft_expected.parent != _pft_bridge_file.parent:
    raise RuntimeError("Known Phoenix visual addon module is unavailable")
_pft_module = _pft_sys.modules.get("pft_visual_tools")
if _pft_module is not None:
    _pft_cached_file = getattr(_pft_module, "__file__", None)
    if not isinstance(_pft_cached_file, str) or _pft_resolve(_pft_cached_file) != _pft_expected:
        raise RuntimeError("Unrecognized cached Phoenix visual addon module")
else:
    _pft_spec = _pft_importlib.spec_from_file_location("pft_visual_tools", _pft_expected)
    if _pft_spec is None or _pft_spec.loader is None:
        raise RuntimeError("Known Phoenix visual addon module is unavailable")
    _pft_module = _pft_importlib.module_from_spec(_pft_spec)
    _pft_sys.modules["pft_visual_tools"] = _pft_module
    try:
        _pft_spec.loader.exec_module(_pft_module)
    except BaseException:
        if _pft_sys.modules.get("pft_visual_tools") is _pft_module:
            _pft_sys.modules.pop("pft_visual_tools", None)
        raise
if not callable(getattr(_pft_module, "prepare_appearance", None)) or not callable(getattr(_pft_module, "apply_appearance", None)):
    raise RuntimeError("Known Phoenix visual addon module is unavailable")
_pft_kwargs = {"transparency": _pft_payload["transparency"]} if "transparency" in _pft_payload else {}
_pft_plan = _pft_module.prepare_appearance(_pft_App, _pft_Gui, _pft_payload["document"], _pft_payload["objects"], _pft_payload["operation"], **_pft_kwargs)
result = _pft_module.apply_appearance(_pft_App, _pft_Gui, _pft_plan)
`;
  if (code.length > 32768) throw new PftBridgeError('INVALID_ARGUMENT', '外观请求超过固定脚本大小限制，请减少对象。');
  return code;
}

const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Preserve the native script job state; pending is acceptance, not completion. */
export function pftAppearanceJob(value: unknown, input: PftAppearanceInput): Record<string, unknown> {
  if (!isRecord(value) || value.request_id !== input.requestId || typeof value.state !== 'string'
    || !['queued', 'starting', 'running', 'done', 'failed', 'cancelled'].includes(value.state)) {
    throw new PftBridgeError('INVALID_RESPONSE', '外观请求没有返回可确认的脚本任务状态。');
  }
  if (value.state === 'failed' || value.state === 'cancelled') {
    throw new PftBridgeError(value.state === 'cancelled' ? 'SCRIPT_CANCELLED' : 'SCRIPT_FAILED', '外观脚本未完成；请用同一 requestId 查询状态并核对当前外观。');
  }
  if (value.state === 'done') {
    const summary = value.result;
    if (!isRecord(summary) || summary.document !== input.document || summary.action !== input.operation
      || summary.target_count !== input.objects.length || !Number.isSafeInteger(summary.changed_count)
      || (summary.changed_count as number) < 0 || (summary.changed_count as number) > input.objects.length
      || summary.saved !== false || summary.recomputed !== false || typeof summary.replayed !== 'boolean') {
      throw new PftBridgeError('INVALID_RESPONSE', '外观任务已结束，但没有返回有效的操作摘要；请查询同一 requestId 并核对外观。');
    }
  }
  return value;
}

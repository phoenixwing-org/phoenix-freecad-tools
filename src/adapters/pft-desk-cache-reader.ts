// SPDX-License-Identifier: Apache-2.0
/** Protocol consumption only. No SQL, cache paths, rendering, or recent workspace discovery. */
import { createHash } from 'node:crypto';
import { PftDeskCacheError, pftRequireCacheFeature, pftDecodeCacheMetadata } from '../pft-desk-cache.js';
import { sanitizePftPng } from '../pft-png.js';
import type { PftDeskHttpResponse } from './pft-desk-http.js';

export type PftDeskCacheSession = {
  expectedIdentity: { instance: string; runtime: string };
  json: (route: string, body?: Buffer) => Promise<unknown>;
  png: (route: string) => Promise<PftDeskHttpResponse>;
};
export async function pftReadCachedMetadata(session: PftDeskCacheSession, workspace: string, rels: string[]) {
  pftRequireCacheFeature(await session.json('/api/runtime/features'), session.expectedIdentity);
  return pftDecodeCacheMetadata(await session.json('/api/cad/cache/v1/query',
    Buffer.from(JSON.stringify({ workspace, rels }), 'utf8')), rels);
}
export async function pftReadCachedThumbnail(session: PftDeskCacheSession, workspace: string, rel: string, revision: string) {
  const metadata = await pftReadCachedMetadata(session, workspace, [rel]);
  const thumbnail = metadata.items[0]!.thumbnail;
  if (thumbnail.state !== 'record') {
    const errors = { missing: 'DESK_CACHE_MISSING', stale: 'DESK_CACHE_STALE',
      unsupported: 'DESK_CACHE_UNSUPPORTED', invalid: 'DESK_CACHE_INVALID' } as const;
    throw new PftDeskCacheError(errors[thumbnail.state]);
  }
  if (thumbnail.revision !== revision || !thumbnail.image) throw new PftDeskCacheError('DESK_CACHE_CHANGED');
  const image = thumbnail.image;
  const params = new URLSearchParams({ workspace, rel, revision });
  const response = await session.png('/api/cad/cache/v1/thumbnail?' + params.toString());
  const headers = response.headers;
  if (response.status !== 200 || !/^image\/png(?:\s*;|\s*$)/iu.test(headers['content-type'] ?? '')
    || headers['content-length'] !== String(image.bytes) || response.bytes.length !== image.bytes
    || headers['cache-control'] !== 'no-store' || headers['x-content-type-options'] !== 'nosniff'
    || headers['x-desk-cache-schema'] !== '1' || headers['x-desk-cache-revision'] !== revision
    || headers['x-desk-image-sha256'] !== image.sha256 || headers.etag !== '"sha256:' + image.sha256 + '"'
    || headers['x-desk-source-freshness'] !== thumbnail.source.freshness
    || createHash('sha256').update(response.bytes).digest('hex') !== image.sha256) {
    throw new PftDeskCacheError('DESK_CACHE_INVALID_RESPONSE');
  }
  const sanitized = sanitizePftPng(response.bytes, { expectedWidth: image.width, expectedHeight: image.height,
    limits: { maxOutputBytes: 600 * 1024 } });
  return { rel, revision, mime_type: 'image/png' as const, data: sanitized.bytes.toString('base64'),
    width: sanitized.width, height: sanitized.height, bytes: sanitized.bytes.length,
    cached_image_sha256: image.sha256, sanitized_image_sha256: sanitized.sha256,
    image_integrity: 'sha256_verified_and_png_structure_checked', image_metadata_removed: true,
    source: thumbnail.source, data_source: 'stored_desk_cache', native_memory: false,
    image_scope: 'saved_thumbnail_not_live_view' };
}

// SPDX-License-Identifier: Apache-2.0
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { addonCli } from './install-addon.mjs';

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) addonCli('uninstall');

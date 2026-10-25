// SPDX-License-Identifier: Apache-2.0
export class PftDeskError extends Error {
  constructor(public readonly code: string, message: string) { super(message); this.name = 'PftDeskError'; }
}

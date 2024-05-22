/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

import type {Hash} from './types';

import serverAPI from './ClientToServerAPI';
import {configBackedAtom} from './jotaiUtils';
import foundPlatform from './platform';
import {showToast} from './toast';

export const supportsBrowseUrlForHash = configBackedAtom(
  'fbcodereview.code-browser-url',
  /* default */ false,
  /* readonly */ true,
  /* use raw value */ true,
);

export async function openBrowseUrlForHash(hash: Hash) {
  serverAPI.postMessage({type: 'getRepoUrlAtHash', hash});
  const msg = await serverAPI.nextMessageMatching('gotRepoUrlAtHash', () => true);

  const url = msg.url;
  if (url.error) {
    showToast('Failed to get repo URL to browse', {durationMs: 5000});
    return;
  } else if (url.value == null) {
    return;
  }
  foundPlatform.openExternalLink(url.value);
}

/**
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

import {useRef, useEffect} from 'react';
import {atom, selector, useSetRecoilState} from 'recoil';

export const mainContentWidthState = atom({
  key: 'mainContentWidthState',
  default: 500,
});

export function useMainContentWidth() {
  const setMainContentWidth = useSetRecoilState(mainContentWidthState);

  const mainContentRef = useRef<null | HTMLDivElement>(null);
  useEffect(() => {
    const element = mainContentRef.current;
    if (element == null) {
      return;
    }

    const obs = new ResizeObserver(entries => {
      const [entry] = entries;
      setMainContentWidth(entry.contentRect.width);
    });
    obs.observe(element);
    return () => obs.unobserve(element);
  }, [mainContentRef, setMainContentWidth]);

  return mainContentRef;
}

export const NARROW_COMMIT_TREE_WIDTH = 800;

export const isNarrowCommitTree = selector({
  key: 'isNarrowCommitTree',
  get: ({get}) => get(mainContentWidthState) < NARROW_COMMIT_TREE_WIDTH,
});

#debugruntest-compatible
# Portions Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2.

# Copyright (c) Mercurial Contributors.

  $ setconfig devel.segmented-changelog-rev-compat=true
  $ setconfig workingcopy.ruststatus=false

  $ setconfig 'extensions.treemanifest=!' hint.ack-match-full-traversal=true
  $ hg init repo
  $ cd repo
  $ echo 0 > a
  $ echo 0 > b
  $ echo 0 > t.h
  $ mkdir t
  $ echo 0 > t/x
  $ echo 0 > t/b
  $ echo 0 > t/e.h
  $ mkdir dir.h
  $ echo 0 > dir.h/foo

  $ hg ci -A -m m
  adding a
  adding b
  adding dir.h/foo
  adding t.h
  adding t/b
  adding t/e.h
  adding t/x

  $ touch nottracked

  $ hg locate a
  a

  $ hg locate NONEXISTENT
  [1]

  $ hg locate
  a
  b
  dir.h/foo
  t.h
  t/b
  t/e.h
  t/x

  $ hg rm a
  $ hg ci -m m

  $ hg locate a
  [1]
  $ hg locate NONEXISTENT
  [1]
  $ hg locate 'relpath:NONEXISTENT'
  [1]
  $ hg locate
  b
  dir.h/foo
  t.h
  t/b
  t/e.h
  t/x
  $ hg locate -r 0 a
  a
  $ hg locate -r 0 NONEXISTENT
  [1]
  $ hg locate -r 0 'relpath:NONEXISTENT'
  [1]
  $ hg locate -r 0
  a
  b
  dir.h/foo
  t.h
  t/b
  t/e.h
  t/x

# -I/-X with relative path should work:

  $ cd t
  $ hg locate
  b
  dir.h/foo
  t.h
  t/b
  t/e.h
  t/x
  $ hg locate -I ../t
  t/b
  t/e.h
  t/x

# Issue294: hg remove --after dir fails when dir.* also exists

  $ cd ..
  $ rm -r t

  $ hg rm t/b

  $ hg locate 't/**'
  t/b
  t/e.h
  t/x

  $ hg files
  b
  dir.h/foo
  t.h
  t/e.h
  t/x
  $ hg files b
  b

  $ mkdir otherdir
  $ cd otherdir

  $ hg files 'path:'
  ../b
  ../dir.h/foo
  ../t.h
  ../t/e.h
  ../t/x
  $ hg files 'path:.'
  ../b
  ../dir.h/foo
  ../t.h
  ../t/e.h
  ../t/x

  $ hg locate b
  ../b
  ../t/b
  $ hg locate '*.h'
  ../t.h
  ../t/e.h
  $ hg locate 'path:t/x'
  ../t/x
  $ hg locate 're:.*\.h$'
  ../t.h
  ../t/e.h
  $ hg locate -r 0 b
  ../b
  ../t/b
  $ hg locate -r 0 '*.h'
  ../t.h
  ../t/e.h
  $ hg locate -r 0 'path:t/x'
  ../t/x
  $ hg locate -r 0 're:.*\.h$'
  ../t.h
  ../t/e.h

  $ hg files
  ../b
  ../dir.h/foo
  ../t.h
  ../t/e.h
  ../t/x
  $ hg files .
  [1]

# Convert native path separator to slash (issue5572)

  $ hg files -T '{path|slashpath}\n'
  ../b
  ../dir.h/foo
  ../t.h
  ../t/e.h
  ../t/x

  $ cd ../..

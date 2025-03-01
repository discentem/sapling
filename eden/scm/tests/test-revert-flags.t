#chg-compatible
#debugruntest-compatible

#require execbit

  $ eagerepo
  $ hg init repo
  $ cd repo
  $ echo foo > foo
  $ chmod 644 foo
  $ hg ci -qAm '644'

  $ chmod 755 foo
  $ hg ci -qAm '755'

reverting to rev 0

  $ hg revert -a -r 'desc(644)'
  reverting foo
  $ hg st
  M foo
  $ hg diff --git
  diff --git a/foo b/foo
  old mode 100755
  new mode 100644

  $ cd ..

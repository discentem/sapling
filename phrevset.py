# phrevset.py - support for Phabricator revsets
#
# Copyright 2013 Facebook, Inc.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""provides support for Phabricator revsets

Allows for queries such as `hg log -r D1234567` to find the commit which
corresponds to a specific Differential revision.
Automatically handles commits already in subversion, or whose hash has
changed since submitting to Differential (due to amends or rebasing).

Requires arcanist to be installed and properly configured.
Repositories should include a callsign in their hgrc.

Example for www:

[phrevset]
callsign = E

"""

from mercurial import extensions
from mercurial import revset
from mercurial import util as hgutil

from hgsubversion import util as svnutil

import os
import signal
import json
import re
import subprocess

DIFFERENTIAL_REGEX = re.compile(
    'Differential Revision: http.+?/'  # Line start, URL
    'D(?P<id>[0-9]+)',  # Differential ID, just numeric part
    flags = re.LOCALE
)

SVN_DESCRIPTION_REGEX = re.compile(
    'Commit r'  # Prefix
    '(?P<callsign>[^0-9]{1,})'  # Callsign
    '(?P<id>[0-9]+)',  # SVN rev
    flags = re.LOCALE
)

def getdiff(repo, diffid):
    """Perform a Conduit API call by shelling out to `arc`

    Returns a subprocess.Popen instance"""

    try:
        proc = subprocess.Popen(['arc', 'call-conduit', 'differential.getdiff'],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, preexec_fn=os.setsid)

        input = json.dumps({'revision_id': diffid})
        repo.ui.debug("[diffrev] echo '%s' | arc call-conduit differential.getdiff\n" %
                      input)
        proc.stdin.write(input)
        proc.stdin.close()

        return proc
    except Exception, e:
        raise hgutil.Abort('Could not not call "arc call-conduit": %s' % e)

def finddiff(repo, diffid, proc=None):
    """Scans the changelog for commit lines mentioning the Differential ID

    If the optional proc paramater is provided, it must be a subprocess.Popen
    instance. It will be polled during the iteration and if it indicates that
    the process has returned, the function will raise StopIteration"""

    repo.ui.debug('[diffrev] Traversing log for %s\n' % diffid)

    # traverse the changelog backwards
    for rev in repo.changelog.revs(start=len(repo.changelog), stop=0):
        if rev % 100 == 0 and proc and proc.poll() is not None:
            raise StopIteration("Parallel proc call completed")

        changectx = repo[rev]
        desc = changectx.description()
        match = DIFFERENTIAL_REGEX.search(desc)

        if match and match.group('id') == diffid:
            return changectx.rev()

    return None

def forksearch(repo, diffid):
    """Perform a log traversal and Conduit call in parallel

    Returns a (revision, arc_response) tuple, where one of the items will be
    None, depending on which process terminated first"""

    repo.ui.debug('[diffrev] Starting Conduit call\n')

    proc = getdiff(repo, diffid)

    try:
        repo.ui.debug('[diffrev] Starting log walk\n')
        rev = finddiff(repo, diffid, proc)

        repo.ui.debug('[diffrev] Parallel log walk completed with %s\n' % rev)
        os.killpg(proc.pid, signal.SIGTERM)

        if rev is None:
            # walked the entire repo and couldn't find the diff
            return ([], None)

        return ([rev], None)

    except StopIteration:
        # search terminated because arc returned
        # if returncode == 0, return arc's output

        repo.ui.debug('[diffrev] Conduit call returned %i\n' % proc.returncode)

        if proc.returncode != 0:
            raise hgutil.Abort('arc call returned status %i' % proc.returncode)

        resp = proc.stdout.read()
        return (None, resp)

def revsetdiff(repo, subset, diffid):
    """Return a set of revisions corresponding to a given Differential ID """

    rev, resp = forksearch(repo, diffid)

    if rev is not None:
        # The log walk found the diff, nothing more to do
        return rev

    jsresp = json.loads(resp)
    if not jsresp:
        raise hgutil.Abort('Could not decode Conduit response')

    resp = jsresp.get('response')
    if not resp:
        error = jsresp.get('errorMessage', 'unknown error')
        raise hgutil.Abort('Counduit error: %s' % error)

    vcs = resp.get('sourceControlSystem')

    repo.ui.debug('[diffrev] VCS is %s\n' % vcs)

    if vcs == 'svn':
        # commit has landed in svn, parse the description to get the SVN
        # revision and delegate to hgsubversion for the rest

        desc = resp['description']
        match = SVN_DESCRIPTION_REGEX.match(desc)

        if not match:
            raise hgutil.Abort("Cannot parse Conduit SVN description '%s'"
                               % desc)

        callsign = match.group('callsign')
        repo_callsign = repo.ui.config('phrevset', 'callsign')

        if callsign != repo_callsign:
            raise hgutil.Abort("Diff callsign '%s' is different from repo"
                               " callsign '%s'" % (callsign, repo_callsign))

        svnrev = match.group('id')
        repo.ui.debug("[diffrev] SVN rev is r%s\n" % svnrev)

        args = ('string', svnrev)
        return svnutil.revset_svnrev(repo, subset, args)

    elif vcs == 'hg':
        # commit is still in hg, get its hash

        props = resp['properties']
        commits = props['local:commits']

        # the JSON parser returns Unicode strings, convert to `str` in UTF-8
        revs = [c['commit'].encode('utf-8') for c in commits.values()]

        # verify all revisions exist in the current repo; if not, try to
        # find their counterpart by parsing the log
        for idx, rev in enumerate(revs):
            if rev not in repo:
                parsed_rev = finddiff(repo, diffid)

                if not parsed_rev:
                    raise hgutil.Abort('Could not find diff '
                                       'D%s in changelog' % diffid)

                revs[idx] = parsed_rev

        return set(revs)

    else:
        if not vcs:
            msg = "D%s does not have an associated version control system\n" \
                  "You can view the diff at http://phabricator.fb.com/D%s\n\n"
            repo.ui.warn(msg % (diffid, diffid))

            return []
        else:
            raise hgutil.Abort('Conduit returned unknown '
                               'sourceControlSystem "%s"' % vcs)

def revsetstringset(orig, repo, subset, revstr):
    """Wrapper that recognizes revisions starting with 'D'"""

    if revstr.startswith('D') and revstr[1:].isdigit():
        return revsetdiff(repo, subset, revstr[1:])

    return orig(repo, subset, revstr)

def extsetup(ui):
    extensions.wrapfunction(revset, 'stringset', revsetstringset)

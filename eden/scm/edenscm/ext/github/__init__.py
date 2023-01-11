# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2.

"""utilities for interacting with GitHub (EXPERIMENTAL)
"""

import shutil
import asyncio
from typing import Optional, List, Dict

from edenscm import error, registrar, util, cmdutil
from edenscm.i18n import _
from .pullrequeststore import PullRequestStore
from edenscm.ext.github.submit import CommitData, derive_commit_data
from edenscm.ext.smartlog import getdag, getrevs

from . import (
    follow,
    github_repo_util,
    import_pull_request,
    link,
    pr_status,
    submit,
    templates,
)
from .github_repo_util import find_github_repo

cmdtable = {}
command = registrar.command(cmdtable)
templatekeyword = registrar.templatekeyword()


def extsetup(ui):
    pr_status.setup_smartset_prefetch()


@command(
    "pr",
    [],
    _("<submit|get|link|unlink|land|...>"),
)
def pull_request_command(ui, repo, *args, **opts):
    """exchange local commit data with GitHub pull requests"""
    raise error.Abort(
        _(
            "you need to specify a subcommand (run with --help to see a list of subcommands)"
        )
    )


subcmd = pull_request_command.subcommand(
    categories=[
        (
            "Create or update pull requests, using `pull` to import a PR, if necessary",
            ["submit", "pull", "land"],
        ),
        (
            "Manually manage associations with pull requests",
            ["follow", "link", "unlink"],
        ),
        ("Wrappers for the GitHub CLI (`gh`)", ["list"]),
    ]
)


@subcmd(
    "s|submit",
    [
        (
            "s",
            "stack",
            False,
            _("also include draft ancestors"),
        ),
        ("m", "message", None, _("message describing changes to updated commits")),
        ("d", "draft", False, _("mark new pull requests as draft")),
    ],
)
def submit_cmd(ui, repo, *args, **opts):
    """create or update GitHub pull requests from local commits"""
    return submit.submit(ui, repo, *args, **opts)

async def getCommitData(ui, repo, *args, **opts) -> List[List[CommitData]]:
    store = PullRequestStore(repo)
    commits_to_process = await asyncio.gather(
        *[
            derive_commit_data(node, repo, store)
            for node in repo.nodes("all()")
        ]
    )
    if not commits_to_process:
        ui.status_err(_("no commits to submit\n"))
        return 0

    # Partition the chain.
    partitions: List[List[CommitData]] = []
    for commit in commits_to_process:
        if commit.is_dep:
            if partitions:
                partitions[-1].append(commit)
            else:
                # If the top of the stack is a "dep commit", then do not
                # submit it.
                continue
        else:
            partitions.append([commit])
    return partitions

@subcmd(
    "l|land",
    [
        (
            "n",
            "number",
            0,
            _("PR to land"),
        )
    ],
)
def land_cmd(ui, repo, *args, **opts):
    """Land a pull request from the commandline"""
    cd = asyncio.run(
        getCommitData(ui, repo, *args, **opts)
    )
    """
    [[CommitData(node=b'(T!\xae\x83Y\x8c\x83p\x81\x97\x84\xb1\xfa\xb4o\x04\x81\x10K', head_branch_name='pr15', pr=PullRequestDetails(node_id='PR_kwDOIsewis5G8yRN', number=15, url='https://github.com/discentem/arborlocker/pull/15', base_oid='e9a18602ec38bb9a1d6c146ae182c5c070da1a84', base_branch_name='main', head_oid='285421ae83598c8370819784b1fab46f0481104b', head_branch_name='pr15', body='fmt.print\n\n---\nStack created with [Sapling](https://sapling-scm.com). Best reviewed with [ReviewStack](https://reviewstack.dev/discentem/arborlocker/pull/15).\n* #16\n* __->__ #15\n'), ctx=<changectx 285421ae8359>, is_dep=False, msg=None)], 
     [CommitData(node=b'~\xb7\xd8\xff\xa1\x81DC\xa3K\xaf\x81\xf9~\xb1\xd6\xb8\x9fI\xbf', head_branch_name='pr16', pr=PullRequestDetails(node_id='PR_kwDOIsewis5G8yoB', number=16, url='https://github.com/discentem/arborlocker/pull/16', base_oid='285421ae83598c8370819784b1fab46f0481104b', base_branch_name='pr15', head_oid='7eb7d8ffa1814443a34baf81f97eb1d6b89f49bf', head_branch_name='pr16', body='moar printing\n\n---\nStack created with [Sapling](https://sapling-scm.com). Best reviewed with [ReviewStack](https://reviewstack.dev/discentem/arborlocker/pull/16).\n* __->__ #16\n* #15\n'), ctx=<changectx 7eb7d8ffa181>, is_dep=False, msg=None)]]
    """

    '''
    def inner():
        for j in ...:
            for k in ...:
                if something:
                    return
    for i in ...:
        inner()
    
    '''
    # https://stackoverflow.com/questions/34964878/python-generate-a-dictionarytree-from-a-list-of-tuples
    stacks = []
    def parseCommitList(commitList):
        for commit in commitList:
            if commit.pr == None:
                continue
            stacks.append({commit.head_branch_name: commit.pr.base_branch_name})
        return
    for commitList in cd:
        parseCommitList(commitList)
    breakpoint()
    raise error.Abort(_("pr land is not implemented yet"))

    # Check if pr.land.mergestrategy == top, if not abort (only supported strategy for now)
    # Get list of PRs (1,2, 3)
    # Leave comment on each of the PRs, except top. For (1,2,3) leave comments on 1 & 2.
     # Comment mentions this are closed and folded into #3
     # if any comment fails, undo previous comments
    # Close 1,2
    # merge 3

@subcmd(
    "pull",
    [
        (
            "g",
            "goto",
            False,
            _("goto the pull request after importing it"),
        )
    ],
    _("PULL_REQUEST"),
)
def pull_cmd(ui, repo, *args, **opts):
    """import a pull request into your working copy

    The PULL_REQUEST can be specified as either a URL:

        https://github.com/facebook/sapling/pull/321

    or just the PR number within the GitHub repository identified by
    `sl config paths.default`.
    """
    ui.note(_("experimental command: this functionality may be folded into pull/goto"))
    return import_pull_request.get_pr(ui, repo, *args, **opts)


@subcmd(
    "link",
    [("r", "rev", "", _("revision to link"), _("REV"))],
    _("[-r REV] PULL_REQUEST"),
)
def link_cmd(ui, repo, *args, **opts):
    """identify a commit as the head of a GitHub pull request

    A PULL_REQUEST can be specified in a number of formats:

    - GitHub URL to the PR: https://github.com/facebook/react/pull/42

    - Integer: Number for the PR. Uses 'paths.upstream' as the target repo,
        if specified; otherwise, falls back to 'paths.default'.
    """
    return link.link(ui, repo, *args, **opts)


@subcmd(
    "unlink",
    [
        ("r", "rev", [], _("revisions to unlink")),
    ],
    _("[OPTION]... [-r] REV..."),
)
def unlink_cmd(ui, repo, *revs, **opts):
    """remove a commit's association with a GitHub pull request"""
    revs = list(revs) + opts.pop("rev", [])
    return link.unlink(ui, repo, *revs)


@subcmd(
    "follow",
    [
        ("r", "rev", [], _("revisions to follow the next pull request")),
    ],
    _("[OPTION]... [-r] REV..."),
)
def follow_cmd(ui, repo, *revs, **opts):
    """join the nearest desecendant's pull request

    Marks commits to become part of their nearest desecendant's pull request
    instead of starting as the head of a new pull request.

    Use `pr unlink` to undo.
    """
    revs = list(revs) + opts.pop("rev", [])
    return follow.follow(ui, repo, *revs)


@subcmd(
    "list",
    [
        ("", "app", "", _("filter by GitHub App author"), _("string")),
        ("a", "assignee", "", _("filter by assignee"), _("string")),
        ("A", "author", "", _("filter by author"), _("string")),
        ("B", "base", "", _("filter by base branch"), _("string")),
        ("d", "draft", False, _("filter by draft state")),
        ("H", "head", "", _("filter by head branch"), _("string")),
        (
            "q",
            "jq",
            "",
            _("filter JSON output using a jq expression"),
            _("expression"),
        ),
        ("", "json", "", _("output JSON with the specified fields"), _("fields")),
        ("l", "label", "", _("filter by label"), _("strings")),
        (
            "L",
            "limit",
            30,
            _("maximum number of items to fetch (default 30)"),
            _("int"),
        ),
        ("S", "search", "", _("search pull requests with query"), _("query")),
        (
            "s",
            "state",
            "",
            _('filter by state: {open|closed|merged|all} (default "open")'),
            _("string"),
        ),
        (
            "t",
            "template",
            "",
            _('format JSON output using a Go template; see "gh help formatting"'),
            _("string"),
        ),
        ("w", "web", False, _("list pull requests in the web browser")),
    ],
)
def list_cmd(ui, repo, *args, **opts) -> int:
    """calls `gh pr list [flags]` with the current repo as the value of --repo"""
    github_repo = find_github_repo(repo).ok()
    if not github_repo:
        raise error.Abort(_("This does not appear to be a GitHub repo."))

    argv0 = _find_gh_cli()
    if argv0 is None:
        raise error.Abort(_("Path to `gh` could not be found."))

    gh_args = [argv0, "--repo", github_repo.as_gh_repo_arg(), "pr", "list"]
    for opt, value in opts.items():
        if value:
            val_type = type(value)
            if val_type == str:
                gh_args.extend([f"--{opt}", value])
            elif val_type == int:
                gh_args.extend([f"--{opt}", str(value)])
            elif val_type == bool:
                gh_args.append(f"--{opt}")
            else:
                raise ValueError(f"unsupported type {val_type} for {value}")

    # Once chg supports an execv-style API, call it with `argv0` and `gh_args`.
    cmd = " ".join([util.shellquote(arg) for arg in gh_args])
    rc = ui.system(cmd)
    return rc


def _find_gh_cli() -> Optional[str]:
    return shutil.which("gh")


@templatekeyword("github_repo")
def github_repo(repo, ctx, templ, **args) -> bool:
    return github_repo_util.is_github_repo(repo)


def _get_pull_request_field(field_name: str, repo, ctx, **args):
    pull_request_data = templates.get_pull_request_data_for_rev(repo, ctx, **args)
    return pull_request_data[field_name] if pull_request_data else None


@templatekeyword("github_pull_request_state")
def github_pull_request_state(repo, ctx, templ, **args) -> Optional[str]:
    return _get_pull_request_field("state", repo, ctx, **args)


@templatekeyword("github_pull_request_closed")
def github_pull_request_closed(repo, ctx, templ, **args) -> Optional[bool]:
    return _get_pull_request_field("closed", repo, ctx, **args)


@templatekeyword("github_pull_request_merged")
def github_pull_request_merged(repo, ctx, templ, **args) -> Optional[bool]:
    return _get_pull_request_field("merged", repo, ctx, **args)


@templatekeyword("github_pull_request_review_decision")
def github_pull_request_review_decision(repo, ctx, templ, **args) -> Optional[str]:
    return _get_pull_request_field("reviewDecision", repo, ctx, **args)


@templatekeyword("github_pull_request_is_draft")
def github_pull_request_is_draft(repo, ctx, templ, **args) -> Optional[bool]:
    return _get_pull_request_field("isDraft", repo, ctx, **args)


@templatekeyword("github_pull_request_title")
def github_pull_request_title(repo, ctx, templ, **args) -> Optional[str]:
    return _get_pull_request_field("title", repo, ctx, **args)


@templatekeyword("github_pull_request_body")
def github_pull_request_body(repo, ctx, templ, **args) -> Optional[str]:
    return _get_pull_request_field("body", repo, ctx, **args)


@templatekeyword("github_pull_request_status_check_rollup")
def github_pull_request_status_check_rollup(repo, ctx, templ, **args) -> Optional[str]:
    pull_request = templates.get_pull_request_data_for_rev(repo, ctx, **args)
    try:
        commit = pull_request["commits"]["nodes"][0]["commit"]
        return commit["statusCheckRollup"]["state"]
    except Exception:
        return None


@templatekeyword("github_pull_request_url")
def github_pull_request_url(repo, ctx, templ, **args) -> Optional[str]:
    """If the commit is associated with a GitHub pull request, returns the URL
    for the pull request.
    """
    pull_request = templates.get_pull_request_url_for_rev(repo, ctx, **args)
    if pull_request:
        pull_request_domain = repo.ui.config("github", "pull_request_domain")
        return pull_request.as_url(domain=pull_request_domain)
    else:
        return None


@templatekeyword("github_pull_request_repo_owner")
def github_pull_request_repo_owner(repo, ctx, templ, **args) -> Optional[str]:
    """If the commit is associated with a GitHub pull request, returns the
    repository owner for the pull request.
    """
    return templates.github_pull_request_repo_owner(repo, ctx, **args)


@templatekeyword("github_pull_request_repo_name")
def github_pull_request_repo_name(repo, ctx, templ, **args) -> Optional[str]:
    """If the commit is associated with a GitHub pull request, returns the
    repository name for the pull request.
    """
    return templates.github_pull_request_repo_name(repo, ctx, **args)


@templatekeyword("github_pull_request_number")
def github_pull_request_number(repo, ctx, templ, **args) -> Optional[int]:
    """If the commit is associated with a GitHub pull request, returns the
    number for the pull request.
    """
    return templates.github_pull_request_number(repo, ctx, **args)


@templatekeyword("sapling_pr_follower")
def sapling_pr_follower(repo, ctx, templ, **args) -> bool:
    """Indicates if this commit is part of a pull request, but not the head commit."""
    store = templates.get_pull_request_store(repo, args["cache"])
    return store.is_follower(ctx.node())

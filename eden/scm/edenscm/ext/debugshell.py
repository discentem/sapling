# Portions Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2.

# Copyright 2010 Mercurial Contributors
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# debugshell extension
"""a python shell with repo, changelog & manifest objects"""

from __future__ import absolute_import

import shlex
import sys
import time
from typing import Any, Dict, List, Optional

import bindings
import edenscm

import edenscmnative
from edenscm import ext, hgdemandimport, pycompat, registrar, traceimport, util
from edenscm.ext import commitcloud as cc
from edenscm.i18n import _


cmdtable = {}
command = registrar.command(cmdtable)


def _assignobjects(objects, repo) -> None:
    objects.update(
        {
            # Shortcuts
            "b": bindings,
            "e": edenscm,
            "x": ext,
            "td": bindings.tracing.tracingdata,
            # Modules
            "bindings": bindings,
            "edenscm": edenscm,
            "edenscmnative": edenscmnative,
            # Utilities
            "util": edenscm.util,
            "hex": edenscm.node.hex,
            "bin": edenscm.node.bin,
        }
    )
    if repo:
        objects.update(
            {
                "repo": repo,
                "cl": repo.changelog,
                "mf": repo.manifestlog,
                "ml": repo.metalog(),
                "ms": getattr(repo, "_mutationstore", None),
            }
        )

        # Commit cloud service.
        ui = repo.ui
        try:
            # pyre-fixme[16]: Module `commitcloud` has no attribute `token`.
            token = cc.token.TokenLocator(ui).token
            if token is not None:
                # pyre-fixme[19]: Expected 1 positional argument.
                objects["serv"] = cc.service.get(ui, token)
        except Exception:
            pass

        # EdenAPI client
        try:
            objects["api"] = repo.edenapi
        except Exception:
            pass

    # Import other handy modules
    for name in ["os", "sys", "subprocess", "re"]:
        objects[name] = __import__(name)


@command(
    "debugshell|dbsh|debugsh",
    [("c", "command", "", _("program passed in as string"), _("CMD"))],
    optionalrepo=True,
)
def debugshell(ui, repo, *args, **opts):
    command = opts.get("command")

    env = globals()
    env["ui"] = ui
    _assignobjects(env, repo)
    sys.argv = pycompat.sysargv = args

    if command:
        exec(command, env, env)
        return 0
    if args:
        path = args[0]
        with open(path) as f:
            command = f.read()
        env["__file__"] = path
        exec(command, env, env)
        return 0
    elif not ui.interactive():
        command = ui.fin.read()
        exec(command, env, env)
        return 0

    # IPython is incompatible with demandimport.
    with hgdemandimport.deactivated():
        _startipython(ui, repo, env)


def _startipython(ui, repo, env) -> None:
    # IPython requires time.clock. It is missing on Windows. Polyfill it.
    # pyre-fixme[16]: Module `time` has no attribute `clock`.
    if getattr(time, "clock", None) is None:
        time.clock = time.time

    from IPython.terminal.embed import InteractiveShellEmbed
    from IPython.terminal.ipapp import load_default_config

    bannermsg = "loaded repo:  %s\n" "using source: %s" % (
        repo and repo.root or "(none)",
        edenscm.__path__[0],
    ) + (
        "\n\nAvailable variables:\n"
        " e:  edenscm\n"
        " x:  edenscm.ext\n"
        " b:  bindings\n"
        " ui: the ui object\n"
        " c:  run command and take output\n"
    )
    if repo:
        bannermsg += (
            " repo: the repo object\n"
            " serv: commitcloud service\n"
            " api: edenapi client\n"
            " cl: repo.changelog\n"
            " mf: repo.manifestlog\n"
            " ml: repo.svfs.metalog\n"
            " ms: repo._mutationstore\n"
        )
    bannermsg += """
Available IPython magics (auto magic is on, `%` is optional):
 time:   measure time
 timeit: benchmark
 trace:  run and print ASCII trace (better with --trace command flag)
 hg:     run commands inline
"""

    config = load_default_config()
    config.InteractiveShellEmbed = config.TerminalInteractiveShell
    config.InteractiveShell.automagic = True
    config.InteractiveShell.banner2 = bannermsg
    config.InteractiveShell.confirm_exit = False

    if util.istest():
        # Disable history during tests.
        config.HistoryAccessor.enabled = False

    util.mainio.disable_progress()

    globals().update(env)
    shell = InteractiveShellEmbed.instance(
        config=config, user_ns=globals(), user_module=sys.modules[__name__]
    )
    _configipython(ui, shell)
    shell()


def c(args: List[str]) -> bytes:
    """Run command with args and take its output.

    Example::

        c(['log', '-r.'])
        c('log -r.')
        %trace c('log -r.')
    """
    if isinstance(args, str):
        args = shlex.split(args)
    ui = globals()["ui"]
    fin = util.stringio()
    fout = util.stringio()
    bindings.commands.run(["hg"] + args, fin, fout, ui.ferr)
    return fout.getvalue()


def _configipython(ui, ipython) -> None:
    """Set up IPython features like magics"""
    from IPython.core.magic import register_line_magic

    # get_ipython is used by register_line_magic
    get_ipython = ipython.get_ipython  # noqa: F841

    @register_line_magic
    def hg(line):
        args = ["hg"] + shlex.split(line)
        return bindings.commands.run(args, ui.fin, ui.fout, ui.ferr)

    @register_line_magic
    def trace(line, ui=ui, shell=ipython):
        """run and print ASCII trace"""
        code = compile(line, "<magic-trace>", "exec")

        td = bindings.tracing.tracingdata()
        ns = shell.user_ns
        ns.update(globals())
        start = util.timer()
        _execwith(td, code, ns)
        durationmicros = (util.timer() - start) * 1e6
        # hide spans less than 50 microseconds, or 1% of the total time
        asciitrace = td.ascii(int(durationmicros / 100) + 50)
        ui.write_err("%s" % asciitrace)
        if not traceimport.enabled:
            ui.write_err("(use 'debugshell --trace' to enable more detailed trace)\n")
        return td


def _execwith(td, code, ns: Optional[Dict[str, Any]]) -> None:
    with td:
        exec(code, ns)

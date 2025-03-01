# Portions Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2.

# Copyright (c) Mercurial Contributors.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2.


import errno
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile


tempprefix = "hg-hghave-"

checks = {
    "true": (lambda: True, "yak shaving"),
    "false": (lambda: False, "nail clipper"),
}

exes = set()


def check(name, desc, exe: bool = False):
    """Registers a check function for a feature."""

    def decorator(func):
        checks[name] = (func, desc)
        return func

    if exe:
        exes.add(name)

    return decorator


def checkvers(name, desc, vers):
    """Registers a check function for each of a series of versions.

    vers can be a list or an iterator"""

    def decorator(func):
        def funcv(v):
            def f():
                return func(v)

            return f

        for v in vers:
            v = str(v)
            f = funcv(v)
            checks["%s%s" % (name, v.replace(".", ""))] = (f, desc % v)
        return func

    return decorator


def checkexe(name):
    f = lambda name=name: os.path.isfile(f"/bin/{name}") or os.path.isfile(
        f"/usr/bin/{name}"
    )
    checks[name] = (f, f"{name} executable")
    exes.add(name)


checkexe("cmp")
checkexe("dd")
checkexe("diff")
checkexe("echo")
checkexe("env")
checkexe("gunzip")
checkexe("gzip")
checkexe("mkfifo")
checkexe("python3.8")
checkexe("tar")
checkexe("tr")
checkexe("umask")
checkexe("unzip")
checkexe("xargs")


_checkfeaturecache = {}


def checkfeatures(features):
    result = {"error": [], "missing": [], "skipped": []}

    for feature in features:
        if feature.startswith("/"):
            # feature is a path to a binary on POSIX
            if not os.access(feature, os.X_OK):
                result["skipped"].append(f"missing binary: {feature}")
            continue

        negate = feature.startswith("no-")
        if negate:
            feature = feature[3:]

        if feature not in checks:
            result["missing"].append(feature)
            continue

        check, desc = checks[feature]
        available = _checkfeaturecache.get(feature)
        try:
            if available is None:
                available = check()
                _checkfeaturecache[feature] = available
        except Exception:
            result["error"].append("hghave check failed: %s" % feature)
            continue

        if not negate and not available:
            result["skipped"].append("missing feature: %s" % desc)
        elif negate and available:
            result["skipped"].append("system supports %s" % desc)

    return result


def require(features):
    """Require that features are available, exiting if not."""
    result = checkfeatures(features)

    for missing in result["missing"]:
        sys.stderr.write("skipped: unknown feature: %s\n" % missing)
    for msg in result["skipped"]:
        sys.stderr.write("skipped: %s\n" % msg)
    for msg in result["error"]:
        sys.stderr.write("%s\n" % msg)

    if result["missing"]:
        sys.exit(2)

    if result["skipped"]:
        sys.exit(80)

    if result["error"]:
        sys.exit(1)


def matchoutput(cmd, regexp, ignorestatus=False):
    """Return the match object if cmd executes successfully and its output
    is matched by the supplied regular expression.
    """
    r = re.compile(regexp)
    try:
        p = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise
        ret = -1
    ret = p.wait()
    s = p.stdout.read()
    return (ignorestatus or not ret) and r.search(s)


def tempdir():
    # Prefer TESTTMP for detecting fs capabilities on the same mount
    testtmp = os.getenv("TESTTMP")
    return testtmp or tempfile.gettempdir()


@check("baz", "GNU Arch baz client", exe=True)
def has_baz():
    return matchoutput("baz --version 2>&1", rb"baz Bazaar version")


@check("bzr", "Canonical's Bazaar client", exe=True)
def has_bzr():
    try:
        # pyre-fixme[21]: Could not find module `bzrlib`.
        import bzrlib

        # pyre-fixme[21]: Could not find module `bzrlib.bzrdir`.
        import bzrlib.bzrdir

        # pyre-fixme[21]: Could not find module `bzrlib.errors`.
        import bzrlib.errors

        # pyre-fixme[21]: Could not find module `bzrlib.revision`.
        import bzrlib.revision

        # pyre-fixme[21]: Could not find module `bzrlib.revisionspec`.
        import bzrlib.revisionspec

        bzrlib.revisionspec.RevisionSpec
        return bzrlib.__doc__ is not None
    except (AttributeError, ImportError):
        return False


@checkvers("bzr", "Canonical's Bazaar client >= %s", (1.14,))
def has_bzr_range(v):
    major, minor = v.split(".")[0:2]
    try:
        import bzrlib

        return bzrlib.__doc__ is not None and bzrlib.version_info[:2] >= (
            int(major),
            int(minor),
        )
    except ImportError:
        return False


@check("chg", "running with chg")
def has_chg():
    return "CHGHG" in os.environ


_zlibsamples = {
    b"c7667dad766d": "789c4b363733334f494c01522900160b036e",
    b"36a25358b7f16835db5a8e4ecc68328f42": "789c33364b34323536b548324f3"
    "334b330364d49324db44835494d4e06f28c2cd24c8c009b180907",
    b"1e0ed22dfcf821b7368535f0d41099f35a139451aec6dfde551a4808c8fc5f": "789c0dc6c901c0300803b095b89cc23829e0fd4768f592aeec980d9b69fa3e"
    "7e120eca844a151d57bd027ab7cf70167f23253bd9e007160f1121",
}


@check("common-zlib", "common zlib that produces consistent result")
def has_common_zlib():
    import binascii
    import zlib

    return all(
        zlib.compress(k) == binascii.unhexlify(v) for k, v in _zlibsamples.items()
    )


@check("cvs", "cvs client/server", exe=True)
def has_cvs():
    re = rb"Concurrent Versions System.*?server"
    return matchoutput("cvs --version 2>&1", re) and not has_msys()


@check("cvs112", "cvs client/server 1.12.* (not cvsnt)")
def has_cvs112():
    re = rb"Concurrent Versions System \(CVS\) 1.12.*?server"
    return matchoutput("cvs --version 2>&1", re) and not has_msys()


@check("cvsnt", "cvsnt client/server", exe=True)
def has_cvsnt():
    re = rb"Concurrent Versions System \(CVSNT\) (\d+).(\d+).*\(client/server\)"
    return matchoutput("cvsnt --version 2>&1", re)


@check("darcs", "darcs client", exe=True)
def has_darcs():
    return matchoutput("darcs --version", rb"\b2\.([2-9]|\d{2})", True)


@check("mtn", "monotone client (>= 1.0)", exe=True)
def has_mtn():
    return matchoutput("mtn --version", rb"monotone", True) and not matchoutput(
        "mtn --version", rb"monotone 0\.", True
    )


@check("eol-in-paths", "end-of-lines in paths")
def has_eol_in_paths():
    try:
        fd, path = tempfile.mkstemp(dir=tempdir(), prefix=tempprefix, suffix="\n\r")
        os.close(fd)
        os.remove(path)
        return True
    except (IOError, OSError):
        return False


@check("execbit", "executable bit")
def has_executablebit():
    try:
        EXECFLAGS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        fh, fn = tempfile.mkstemp(dir=tempdir(), prefix=tempprefix)
        try:
            os.close(fh)
            m = os.stat(fn).st_mode & 0o777
            new_file_has_exec = m & EXECFLAGS
            os.chmod(fn, m ^ EXECFLAGS)
            exec_flags_cannot_flip = (os.stat(fn).st_mode & 0o777) == m
        finally:
            os.unlink(fn)
    except (IOError, OSError):
        # we don't care, the user probably won't be able to commit anyway
        return False
    return not (new_file_has_exec or exec_flags_cannot_flip)


@check("icasefs", "case insensitive file system")
def has_icasefs():
    # Stolen from edenscm.util
    fd, path = tempfile.mkstemp(dir=tempdir(), prefix=tempprefix)
    os.close(fd)
    try:
        s1 = os.stat(path)
        d, b = os.path.split(path)
        p2 = os.path.join(d, b.upper())
        if path == p2:
            p2 = os.path.join(d, b.lower())
        try:
            s2 = os.stat(p2)
            return s2 == s1
        except OSError:
            return False
    finally:
        os.remove(path)


@check("fifo", "named pipes")
def has_fifo():
    if getattr(os, "mkfifo", None) is None:
        return False
    name = tempfile.mktemp(dir=tempdir(), prefix=tempprefix)
    try:
        os.mkfifo(name)
        os.unlink(name)
        return True
    except OSError:
        return False


@check("normal-layout", "common file layout that hg is not a packed binary")
def has_normal_layout():
    # Cannot test this reliably. So test an environment variable set by the
    # test runner.
    return os.environ.get("HGTEST_NORMAL_LAYOUT", "1") == "1"


@check("killdaemons", "killdaemons.py support")
def has_killdaemons():
    return True


@check("lsprof", "python lsprof module")
def has_lsprof():
    try:
        # pyre-fixme[21]: Could not find module `_lsprof`.
        import _lsprof

        _lsprof.Profiler  # silence unused import warning
        return True
    except ImportError:
        return False


@check("lz4", "lz4 compress module")
def has_lz4():
    try:
        import lz4

        lz4.compress  # silence unused import warning
        return True
    except ImportError:
        return False
    except AttributeError:
        pass
    # modern lz4 has "compress" defined in lz4.block
    try:
        from lz4 import block as lz4block

        lz4block.compress  # silence unused import warning
        return True
    except (ImportError, AttributeError):
        return False


def gethgversion():
    m = matchoutput("hg --version --quiet 2>&1", rb"(\d+)\.(\d+)")
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


@checkvers("hg", "Mercurial >= %s", list([(1.0 * x) / 10 for x in range(9, 99)]))
def has_hg_range(v):
    major, minor = v.split(".")[0:2]
    return gethgversion() >= (int(major), int(minor))


@check("hg08", "Mercurial >= 0.8")
def has_hg08():
    if checks["hg09"][0]():
        return True
    return matchoutput("hg help annotate 2>&1", "--date")


@check("hg07", "Mercurial >= 0.7")
def has_hg07():
    if checks["hg08"][0]():
        return True
    return matchoutput("hg --version --quiet 2>&1", "Mercurial Distributed SCM")


@check("hg06", "Mercurial >= 0.6")
def has_hg06():
    if checks["hg07"][0]():
        return True
    return matchoutput("hg --version --quiet 2>&1", "Mercurial version")


@check("gettext", "GNU Gettext (msgfmt)")
def has_gettext():
    return matchoutput("msgfmt --version", rb"GNU gettext-tools")


@check("git", "git command line client", exe=True)
def has_git():
    return matchoutput("git --version 2>&1", rb"^git version")


def getgitversion():
    m = matchoutput("git --version 2>&1", rb"git version (\d+)\.(\d+)")
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


# https://github.com/git-lfs/lfs-test-server
@check("lfs-test-server", "git-lfs test server")
def has_lfsserver():
    exe = "lfs-test-server"
    if has_windows():
        exe = "lfs-test-server.exe"
    return any(
        os.access(os.path.join(path, exe), os.X_OK)
        for path in os.environ["PATH"].split(os.pathsep)
    )


@checkvers("git", "git client (with ext::sh support) version >= %s", (1.9,))
def has_git_range(v):
    major, minor = v.split(".")[0:2]
    return getgitversion() >= (int(major), int(minor))


@check("docutils", "Docutils text processing library")
def has_docutils():
    try:
        import docutils.core

        docutils.core.publish_cmdline  # silence unused import
        return True
    except ImportError:
        return False


@check("p4", "Perforce server and client", exe=True)
def has_p4():
    return matchoutput("p4 -V", rb"Rev\. P4/") and matchoutput("p4d -V", rb"Rev\. P4D/")


@check("jq", "json processing tool", exe=True)
def has_jq():
    return matchoutput("jq --help", rb"Usage:\W+jq .*")


@check("symlink", "symbolic links")
def has_symlink():
    if os.name == "nt" or getattr(os, "symlink", None) is None:
        return False
    name = tempfile.mktemp(dir=tempdir(), prefix=tempprefix)
    try:
        os.symlink(".", name)
        os.unlink(name)
        return True
    except (OSError, AttributeError):
        return False


@check("hardlink", "hardlinks")
def has_hardlink():
    from edenscm import util

    fh, fn = tempfile.mkstemp(dir=tempdir(), prefix=tempprefix)
    os.close(fh)
    name = tempfile.mktemp(dir=tempdir(), prefix=tempprefix)
    try:
        util.oslink(fn, name)
        os.unlink(name)
        return True
    except OSError:
        return False
    finally:
        os.unlink(fn)


@check("rmcwd", "can remove current working directory")
def has_rmcwd():
    ocwd = os.getcwd()
    temp = tempfile.mkdtemp(dir=tempdir(), prefix=tempprefix)
    try:
        os.chdir(temp)
        # On Linux, 'rmdir .' isn't allowed, but the other names are okay.
        # On Solaris and Windows, the cwd can't be removed by any names.
        os.rmdir(os.getcwd())
        return True
    except OSError:
        return False
    finally:
        os.chdir(ocwd)
        # clean up temp dir on platforms where cwd can't be removed
        try:
            os.rmdir(temp)
        except OSError:
            pass


@check("tla", "GNU Arch tla client", exe=True)
def has_tla():
    return matchoutput("tla --version 2>&1", rb"The GNU Arch Revision")


@check("gpg", "gpg client", exe=True)
def has_gpg():
    return matchoutput("gpg --version 2>&1", rb"GnuPG")


@check("gpg2", "gpg client v2")
def has_gpg2():
    return matchoutput("gpg --version 2>&1", rb"GnuPG[^0-9]+2\.")


@check("gpg21", "gpg client v2.1+")
def has_gpg21():
    return matchoutput("gpg --version 2>&1", rb"GnuPG[^0-9]+2\.(?!0)")


@check("unix-permissions", "unix-style permissions")
def has_unix_permissions():
    d = tempfile.mkdtemp(dir=tempdir(), prefix=tempprefix)
    try:
        fname = os.path.join(d, "foo")
        for umask in (0o77, 0o07, 0o22):
            os.umask(umask)
            f = open(fname, "w")
            f.close()
            mode = os.stat(fname).st_mode
            os.unlink(fname)
            if mode & 0o777 != ~umask & 0o666:
                return False
        return True
    finally:
        os.rmdir(d)


@check("unix-socket", "AF_UNIX socket family")
def has_unix_socket():
    return getattr(socket, "AF_UNIX", None) is not None


@check("root", "root permissions")
def has_root():
    return getattr(os, "geteuid", None) and os.geteuid() == 0


@check("pyflakes", "Pyflakes python linter", exe=True)
def has_pyflakes():
    pyflakespath = os.environ.get("HGTEST_PYFLAKES_PATH", "pyflakes")
    return matchoutput(
        "sh -c \"echo 'import re' 2>&1 | %s\"" % pyflakespath,
        rb"<stdin>:1: 're' imported but unused",
        True,
    )


@check("pylint", "Pylint python linter", exe=True)
def has_pylint():
    return matchoutput("pylint --help", rb"Usage:  pylint", True)


@check("clang-format", "clang-format C code formatter", exe=True)
def has_clang_format():
    return matchoutput(
        "clang-format --help", rb"^OVERVIEW: A tool to format C/C\+\+[^ ]+ code."
    )


@check("jshint", "JSHint static code analysis tool", exe=True)
def has_jshint():
    return matchoutput("jshint --version 2>&1", rb"jshint v")


@check("pygments", "Pygments source highlighting library")
def has_pygments():
    try:
        import pygments

        pygments.highlight  # silence unused import warning
        return True
    except ImportError:
        return False


@check("outer-repo", "outer repo")
def has_outer_repo():
    # failing for other reasons than 'no repo' imply that there is a repo
    return not matchoutput("hg root 2>&1", rb"abort: no repository found", True)


@check("ssl", "ssl module available")
def has_ssl():
    try:
        import ssl

        ssl.CERT_NONE
        return True
    except ImportError:
        return False


@check("sslcontext", "python >= 2.7.9 ssl")
def has_sslcontext():
    try:
        import ssl

        ssl.SSLContext
        return True
    except (ImportError, AttributeError):
        return False


@check("defaultcacerts", "can verify SSL certs by system's CA certs store")
def has_defaultcacerts():
    from edenscm import sslutil, ui as uimod

    ui = uimod.ui.load()
    return sslutil._defaultcacerts(ui) or sslutil._canloaddefaultcerts


@check("defaultcacertsloaded", "detected presence of loaded system CA certs")
def has_defaultcacertsloaded():
    import ssl

    from edenscm import sslutil, ui as uimod

    if not has_defaultcacerts():
        return False
    if not has_sslcontext():
        return False

    ui = uimod.ui.load()
    cafile = sslutil._defaultcacerts(ui)
    ctx = ssl.create_default_context()
    if cafile:
        ctx.load_verify_locations(cafile=cafile)
    else:
        ctx.load_default_certs()

    return len(ctx.get_ca_certs()) > 0


@check("tls1.2", "TLS 1.2 protocol support")
def has_tls1_2():
    from edenscm import sslutil

    return "tls1.2" in sslutil.supportedprotocols


@check("windows", "Windows")
def has_windows():
    return os.name == "nt"


@check("system-sh", "system() uses sh")
def has_system_sh():
    return os.name != "nt"


@check("serve", "platform and python can manage 'hg serve -d'")
def has_serve():
    return True


@check("test-repo", "running tests from repository")
def has_test_repo():
    # test-check-*.t tests. They are confusing as the "hg"
    # might have to be the system hg, not the one for testing.
    # Drop support for them to avoid supporting running tests
    # using system hg.
    # Those tests might want to be written as separate linters
    # instead.
    return False


@check("tic", "terminfo compiler and curses module")
def has_tic():
    try:
        import curses

        curses.COLOR_BLUE
        return matchoutput('test -x "`which tic`"', rb"")
    except ImportError:
        return False


@check("msys", "Windows with MSYS")
def has_msys():
    return os.getenv("MSYSTEM")


@check("aix", "AIX")
def has_aix():
    return sys.platform.startswith("aix")


@check("osx", "OS X")
def has_osx():
    return sys.platform == "darwin"


@check("linux", "Linux")
def has_linux():
    return sys.platform == "linux"


@check("osxpackaging", "OS X packaging tools")
def has_osxpackaging():
    try:
        return (
            matchoutput("pkgbuild", rb"Usage: pkgbuild ", ignorestatus=1)
            and matchoutput("productbuild", rb"Usage: productbuild ", ignorestatus=1)
            and matchoutput("lsbom", rb"Usage: lsbom", ignorestatus=1)
            and matchoutput("xar --help", rb"Usage: xar", ignorestatus=1)
        )
    except ImportError:
        return False


@check("security", "OS X security helper", exe=True)
def has_security():
    return matchoutput("security", rb"security commands are", ignorestatus=1)


@check("linuxormacos", "Linux or MacOS")
def has_linuxormacos():
    # This isn't a perfect test for MacOS. But it is sufficient for our needs.
    return sys.platform.startswith(("linux", "darwin"))


@check("docker", "docker support")
def has_docker():
    pat = rb"A self-sufficient runtime for"
    if matchoutput("docker --help", pat):
        if "linux" not in sys.platform:
            # TODO: in theory we should be able to test docker-based
            # package creation on non-linux using boot2docker, but in
            # practice that requires extra coordination to make sure
            # $TESTTEMP is going to be visible at the same path to the
            # boot2docker VM. If we figure out how to verify that, we
            # can use the following instead of just saying False:
            # return 'DOCKER_HOST' in os.environ
            return False

        return True
    return False


@check("debhelper", "debian packaging tools")
def has_debhelper():
    # Some versions of dpkg say `dpkg', some say 'dpkg' (` vs ' on the first
    # quote), so just accept anything in that spot.
    dpkg = matchoutput("dpkg --version", rb"Debian .dpkg' package management program")
    dh = matchoutput("dh --help", rb"dh is a part of debhelper.", ignorestatus=True)
    dh_py2 = matchoutput("dh_python2 --help", rb"other supported Python versions")
    # debuild comes from the 'devscripts' package, though you might want
    # the 'build-debs' package instead, which has a dependency on devscripts.
    debuild = matchoutput(
        "debuild --help", rb"to run debian/rules with given parameter"
    )
    return dpkg and dh and dh_py2 and debuild


@check("demandimport", "demandimport enabled")
def has_demandimport():
    # chg disables demandimport intentionally for performance wins.
    return (not has_chg()) and os.environ.get("HGDEMANDIMPORT") != "disable"


@check("py2", "running with Python 2.x")
def has_py2():
    return 2 == sys.version_info[0] or "HGTEST_FORCE_PY2" in os.environ


@check("slow", "allow slow tests (use --allow-slow-tests)")
def has_slow():
    return os.environ.get("HGTEST_SLOW") == "slow"


@check("hypothesis", "Hypothesis automated test generation")
def has_hypothesis():
    try:
        # pyre-fixme[21]: Could not find module `hypothesis`.
        import hypothesis

        hypothesis.given
        return True
    except ImportError:
        return False


@check("unziplinks", "unzip(1) understands and extracts symlinks")
def unzip_understands_symlinks():
    return matchoutput("unzip --help", rb"Info-ZIP")


@check("zstd", "zstd Python module available")
def has_zstd():
    try:
        from bindings import zstd

        zstd.apply
        return True
    except ImportError:
        return False


@check("devfull", "/dev/full special file")
def has_dev_full():
    return os.path.exists("/dev/full")


@check("virtualenv", "Python virtualenv support")
def has_virtualenv():
    try:
        # pyre-fixme[21]: Could not find module `virtualenv`.
        import virtualenv

        virtualenv.ACTIVATE_SH
        return True
    except ImportError:
        return False


@check("fsmonitor", "running tests with fsmonitor")
def has_fsmonitor():
    return "HGFSMONITOR_TESTS" in os.environ


@check("fuzzywuzzy", "Fuzzy string matching library")
def has_fuzzywuzzy():
    try:
        # pyre-fixme[21]: Could not find module `fuzzywuzzy`.
        import fuzzywuzzy

        fuzzywuzzy.__version__
        return True
    except ImportError:
        return False


@check("eden", "Eden HG extension")
def has_eden():
    return matchoutput(
        "hg --debug --config extensions.eden= --version 2>&1",
        re.compile(rb"^\s*eden\s+(in|ex)ternal\s*$", re.MULTILINE),
    )


@check("parso", "parso parsing library")
def has_parso():
    try:
        import parso

        parso.parse
        return True
    except ImportError:
        return False


@check("mononoke", "Mononoke server available")
def has_mononoke():
    return "USE_MONONOKE" in os.environ


@check("bucktest", "Tests are being run from Buck")
def has_bucktest():
    return "HGTEST_HG" in os.environ


@check("bash", "running via real bash")
def has_bash():
    return False

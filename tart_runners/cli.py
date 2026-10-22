import argparse
import logging
import os
import pwd
import signal
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config as configuration
from . import paths
from .check import check
from .config import ConfigError, MAX_SLOTS


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tart-runners")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run one VM slot until the code or config changes")
    run.add_argument("--slot", type=int, choices=range(1, MAX_SLOTS + 1), required=True)
    run.add_argument("--config", type=Path, default=paths.CONFIG)
    run.add_argument("--once", action="store_true", help="make one attempt, logging each step to stderr")

    check_ = commands.add_parser("check", help="exit non-zero unless every slot has a runner online")
    check_.add_argument("--config", type=Path, default=paths.CONFIG)
    check_.add_argument("--user", help="the user the runners run as, when checking as root")
    check_.add_argument("--wait", type=int, default=0, help="seconds to wait for runners to come online")

    disable = commands.add_parser("disable", help="stop this Mac taking jobs once its running jobs finish")
    disable.add_argument("--reason", help="shown by check, and so in the installer's output")
    disable.add_argument("--user", help="the user the runners run as, when running as root")

    enable = commands.add_parser("enable", help="undo disable")
    enable.add_argument("--user", help="the user the runners run as, when running as root")

    validate = commands.add_parser("validate", help="validate a config file")
    validate.add_argument("config", type=Path)
    validate.add_argument("--cores", type=int, help="validate for another Mac's core count")
    validate.add_argument("--memory-gb", type=int, help="validate for another Mac's memory")

    install = commands.add_parser("install", help="install a release; run by install.sh")
    install.add_argument("--user", required=True)
    install.add_argument("--config", type=Path, required=True)
    install.add_argument("--release", type=Path, required=True)
    install.add_argument("--wait", type=int, default=600)

    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ConfigError as e:
        print(f"invalid config: {e}", file=sys.stderr)
        return 2


def _run(args):
    from .slot import Slot

    # launchd stops services with SIGTERM; raising lets the slot tear its VM down on the way out.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    home = Path.home()
    slot = Slot(configuration.load(args.config, home=home), args.config, args.slot, home)
    if args.once:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        slot.state.mkdir(parents=True, exist_ok=True)
        slot.logs.mkdir(parents=True, exist_ok=True)
        slot.run_once()
        return 0
    log_dir = paths.log_dir(home)
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / f"slot-{args.slot}.log", maxBytes=10 * 1024**2, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    slot.run_forever()
    return 0


def _check(args):
    home = Path(pwd.getpwnam(args.user).pw_dir) if args.user else Path.home()
    return _report(check(configuration.load(args.config, home=home), home, services=True, wait=args.wait))


def _disable(args):
    marker = paths.disabled_marker(_runner_home(args))
    marker.parent.mkdir(parents=True, exist_ok=True)
    since = datetime.now(timezone.utc).isoformat(timespec="seconds")
    marker.write_text(f"since {since}" + (f": {args.reason}" if args.reason else "") + "\n")
    print("disabled: idle runners stop within a minute, busy ones after their current job")
    return 0


def _enable(args):
    paths.disabled_marker(_runner_home(args)).unlink(missing_ok=True)
    print("enabled: slots start taking jobs again within a few seconds")
    return 0


def _runner_home(args):
    if args.user:
        return Path(pwd.getpwnam(args.user).pw_dir)
    # Root's home would be the wrong place, and the slots would never see the change.
    if os.geteuid() == 0:
        raise SystemExit("running as root: pass --user with the user the runners run as")
    return Path.home()


def _validate(args):
    hardware = configuration.host_hardware()
    hardware = (args.cores or hardware[0], args.memory_gb or hardware[1])
    config = configuration.load(args.config, hardware=hardware)
    print(
        f"{args.config}: ok, {config.slots} slot(s) of {config.vm.cpu} CPU and {config.vm.memory_gb}GB"
        f" for {config.host} with labels {', '.join(config.github.labels)}"
    )
    return 0


def _install(args):
    from .install import install

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return _report(install(args.user, args.config, args.release, args.wait))


def _report(problems):
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print("all runners online")
    return 1 if problems else 0


COMMANDS = {
    "run": _run,
    "check": _check,
    "disable": _disable,
    "enable": _enable,
    "validate": _validate,
    "install": _install,
}

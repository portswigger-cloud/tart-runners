import argparse
import logging
import pwd
import signal
import sys
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

    check_ = commands.add_parser("check", help="exit non-zero unless every slot has a runner online")
    check_.add_argument("--config", type=Path, default=paths.CONFIG)
    check_.add_argument("--user", help="the user the runners run as, when checking as root")
    check_.add_argument("--wait", type=int, default=0, help="seconds to wait for runners to come online")

    validate = commands.add_parser("validate", help="validate a config file")
    validate.add_argument("config", type=Path)

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

    home = Path.home()
    log_dir = paths.log_dir(home)
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / f"slot-{args.slot}.log", maxBytes=10 * 1024**2, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # launchd stops services with SIGTERM; raising lets the slot tear its VM down on the way out.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    Slot(configuration.load(args.config, home=home), args.config, args.slot, home).run_forever()
    return 0


def _check(args):
    home = Path(pwd.getpwnam(args.user).pw_dir) if args.user else Path.home()
    return _report(check(configuration.load(args.config, home=home), home, services=True, wait=args.wait))


def _validate(args):
    config = configuration.load(args.config)
    print(f"{args.config}: ok, {config.slots} slot(s) for {config.host} with labels {', '.join(config.github.labels)}")
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


COMMANDS = {"run": _run, "check": _check, "validate": _validate, "install": _install}

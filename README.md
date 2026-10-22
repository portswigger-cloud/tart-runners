# tart-runners

Ephemeral GitHub Actions runners on Apple Silicon Macs, one [Tart](https://tart.run) VM per job.

Each Mac runs up to two slots (Apple's licence allows two macOS VMs per Mac). A slot loops:

1. Clone the VM image and boot it.
2. Copy in the Actions runner, which the host downloaded and checked against the SHA-256 GitHub publishes.
3. Register a single-use runner with a [JIT config](https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-an-organization), named `<host>-<slot>-<random>`.
4. Run one job, then delete the VM and the registration.

It's plain Python 3.11+ with no dependencies, inspired by [Sand](https://github.com/khoi/sand).

## Configuration

One declarative TOML file describes the whole fleet. See [`examples/config.toml`](examples/config.toml). Each kind of
Mac has a `[machines.<name>]` entry giving its slots, VM size and extra labels, picked by matching the host's cores and
memory exactly. A Mac with no matching entry refuses to install, so a new kind of Mac gets sized on purpose.

Unknown keys are errors, so a typo fails the install rather than being ignored. Check a file with:

```
python3 -m tart_runners validate config.toml --cores 12 --memory-gb 48
```

The GitHub App needs the organization permission *Self-hosted runners: read and write*. Its private key stays on
the host, at the path in `github.private_key`.

## Install

On a Mac with Homebrew, for the user the runners will run as:

```
sudo ./install.sh <user> config.toml
```

This installs Python and Tart with Homebrew, copies the code to `/Library/Application Support/tart-runners`, keeps the
Mac awake, shortens the VM DHCP lease, and loads one launch daemon per slot, running as the user. It also installs a `tart-runners` command. It then waits
up to ten minutes for every slot to have a runner online, and exits non-zero if they don't.

Rerunning it is safe. A slot picks up a new release or config after its current job, so jobs aren't interrupted.

## Operating

```
tart-runners check                                 # are this host's runners online?
tail -f ~/Library/Logs/tart-runners/slot-1.log    # slot log
tail -f ~/Library/Logs/tart-runners/vm-1.log      # tart and runner output
```

A slot that fails retries with backoff, up to five minutes. Its last state is in
`~/Library/Application Support/tart-runners/slot-<n>.json`, and `check` prints it.

## Development

```
python3 -m unittest discover -s tests -t .
```

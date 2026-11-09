# pueue_visualization

A small web dashboard for the tasks in your [pueue](https://github.com/Nukesor/pueue) daemon:
what is running, what is queued (and why), and everything that finished, 20 tasks per page.
It only uses the Python 3.10+ standard library, so there is nothing to install.
All data comes from `pueue status --json`.

| Page | Shows |
|---|---|
| **Overview** `/` | Summary cards (running, paused, queued, stashed, success, failed, killed), a table of groups, and all tasks |
| **Running** `/running` | Running and paused tasks: command, group, start time, run time, working directory |
| **Queue** `/queue` | Queued, stashed and locked tasks: why each is waiting (hover for details), dependencies, how long it has waited |
| **Finished** `/finished` | Final state (Success, Failed (exit code), Killed, Dependency failed, ...), start, end, run time |
| **Task** `/task/<id>` | Every field: command, path, group, label, dependencies, tasks that depend on it, all timestamps |

Every list can be filtered (search command / label / ID, state, group, date added) and sorted by clicking a column header.
Logs are not shown; the task page reminds you of `pueue log <id>` / `pueue follow <id>`.

## Quick start

```bash
cd ~/software/pueue_visualization
tmux new -s pueueviz          # keeps the server running after you disconnect
./run.sh --check              # optional: confirm pueue data can be read
./run.sh                      # default port 8766
```

It prints:

```
  1. On your laptop:  ssh -N -L 8766:localhost:8766 you@server.example.edu
  2. Open:            http://localhost:8766/?token=Xq3...
```

Run the `ssh -N -L ...` line in a terminal on your laptop (it just sits there; `Ctrl+C` closes the tunnel),
then open the URL in your laptop's browser. After the first visit the token is kept in a cookie,
so `http://localhost:8766/` is enough until the server restarts. If you are already on the
server's desktop, just open the URL directly.

If the printed `user@host` is not how you reach the machine, pass `--ssh user@host`.

## Options

| Flag | Default | |
|---|---|---|
| `--port` | 8766 | Uses the next free port if it is taken |
| `--refresh` | 10 | Auto-refresh seconds (0 = off); can also be switched off in the page header |
| `--page-size` | 20 | Tasks per page |
| `--ttl` | 5 | Seconds to reuse `pueue status` output between requests |
| `--config` | | pueue config file, passed to `pueue -c` (for a non-default daemon) |
| `--pueue` | `pueue` | pueue executable |
| `--ssh` | `$USER@<fqdn>` | What to print in the ssh tunnel hint |
| `--host` | 127.0.0.1 | Leave this alone (see Security) |

## Security

Anyone logged into the server can connect to `localhost:<port>`, so the server binds to
`127.0.0.1` only and requires the random token printed at startup (a new one every start).
Requests without it get 403. The app is read-only: it only runs `pueue status --json`.
`pueue status --json` includes each task's full environment; the app drops it while parsing,
so environment variables (which can hold secrets) never reach a page.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests use `pueue status --json` output saved in `tests/fixtures/` (captured from pueue 4.0.4),
so they run without a daemon. The parser also accepts the older pueue 3 JSON layout.

See [modifications.md](modifications.md) for how this differs from the Slurm dashboard it is based on.

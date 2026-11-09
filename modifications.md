# Modifications from slurm_visualization

This project is a port of [slurm_visualization](https://github.com/Beilong-Tang/slurm_visualization)
from Slurm jobs to pueue tasks. The architecture is the same (stdlib-only `http.server`, token
auth, TTL cache, server-rendered HTML with in-place updates from `app.js`); the data source and
the pages changed as below.

## Data source

| Slurm version | pueue version |
|---|---|
| `slurm_viz/slurm.py`: `squeue`, `sacct`, `scontrol`, `sacct -j` steps, merged by job id | `pueue_viz/pueue.py`: a single `pueue status --json` call |
| `Job` dataclass (partition, QoS, nodes, CPUs, memory, GPUs, time limit, ...) | `Task` dataclass: id, command, original command, path, group, label, dependencies, priority, state, exit code, created / enqueued / scheduled / start / end times |
| `SlurmClient`, `SlurmError` | `PueueClient`, `PueueError` |
| 30-day `sacct` history window (`--days`) | Removed: pueue keeps every task until `pueue clean`, so all tasks are shown |

Parsing details:

- pueue 4 encodes state as a tagged enum (`{"Done": {"start", "end", "result": {"Failed": 3}}}`,
  `{"Running": {...}}`, `{"Stashed": {"enqueue_at"}}`, `{"Locked": {"previous_status"}}`).
  The older pueue 3 layout (`"Running"`, `{"Done": "Success"}` with times on the task) is also accepted.
- pueue timestamps have nanoseconds, which Python 3.10's `datetime.fromisoformat` rejects; they are
  trimmed to microseconds. Times are timezone-aware, so the Slurm version's naive-time handling was simplified.
- **The `envs` field (each task's full environment) is discarded while parsing**, so secrets in
  environment variables can never be rendered.
- State mapping: Running/Paused → *running*; Queued/Stashed/Locked → *pending*; Done → *finished*,
  coloured Success (grey), Failed / Failed to spawn / Errored / Dependency failed (red), Killed (purple).
  Paused gets its own colour (teal, which was the GPU colour) and Stashed a brown card.

## Pages

| Slurm | pueue |
|---|---|
| Overview: running / queued / completed / failed / cancelled / GPU cards | Overview: running / paused / queued / stashed / success / failed / killed cards, **plus a Groups table** (status, parallel limit, task counts per state; click to filter) |
| `/running`: node, elapsed vs. time-limit bar, time left | `/running`: running + paused tasks with command, group, start, run time (marked "(paused)" for paused tasks), path |
| `/pending`: Slurm pending reason with explanation | `/queue`: computed **"Why waiting"**: Stashed, Scheduled (with `--delay` time), Locked, Dependency (lists unfinished deps), Group paused, or No free slot (shows N of M running in the group) |
| `/completed`: exit code, end time | `/finished`: state label includes exit code (`Failed (3)`), start, end, run time |
| `/job/<id>`: fields, GPU stats, steps, live stdout/stderr | `/task/<id>`: all fields incl. full command, original command (if different), dependencies and dependents ("Needed by"); auto-refreshes while the task is active |
| Filters: name/ID, state, partition, submit date | Filters: command/label/path/ID (`#7` works), state (or group like `failed`), group, date added |

## Removed (as requested)

- GPU utilization / memory efficiency (`gpu.py`, `slurm-gpu`, `--gpu-stats`) and GPU cards/columns.
- Log viewing (live tails, `/job/<id>/log`, "View full log"); the task page shows the `pueue log` / `pueue follow` commands instead.
- Job steps, partition/QoS/account/node/CPU/memory fields, time limits.
- NCShare-specific login-node / compute-node tunnel text.

## Other changes

- CLI: `--days`, `--user`, `--gpu-stats` removed; added `--config` (passed to `pueue -c`), `--pueue`
  (executable path), and `--ssh` (the `user@host` printed in the tunnel hint, default `$USER@<fqdn>`).
- Defaults: port 8766 (so it can run next to the Slurm dashboard on 8765), refresh 10 s, cache TTL 5 s
  (pueue answers in milliseconds, unlike `sacct`).
- Cookies renamed `pueueviz_<port>` / `pueueviz_tz`; page titles "Pueue Tasks"; green favicon.
- `app.js`: live-log code removed; filter keys use `group` instead of `partition`; in-place
  refresh now also works on the task page (any `main [data-swap]` region).
- `--check` prints task counts by state, groups, and the newest tasks.
- `cache.py` is unchanged.

## Tests

`tests/test_pueue.py` (parsing: every state, nanosecond timestamps, pueue 3 layout, Locked,
FailedToSpawn, env stripping, cache fallback) and `tests/test_views.py` (filters, sorting,
pagination, wait reasons, time zones, server auth/routes/escaping). The fixture
`tests/fixtures/status.json` was captured from a throwaway pueue 4.0.4 daemon with tasks in every
state; its env vars were replaced with dummy values.

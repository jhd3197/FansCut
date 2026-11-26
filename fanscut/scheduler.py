"""Periodic tasks on cron schedules, with run-now, cancel and reschedule.

Schedules live in config.yaml under `schedule:` (standard 5-field cron, local
time; "" turns a task off). One thread checks every few seconds which tasks
are due and runs each on its own thread; a task never overlaps itself.

    schedule:
      prune: "0 5 * * *"          # art cache, old jobs, old catalogue replies
      library_refresh: "*/5 * * * *"
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("fanscut.scheduler")

_FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))  # minute hour dom month dow


class CronError(ValueError):
    pass


def _field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise CronError(f"bad step in {text!r}")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            if not (x.isdigit() and y.isdigit()):
                raise CronError(f"bad range in {text!r}")
            a, b = int(x), int(y)
        elif part.isdigit():
            a = b = int(part)
            if step != 1:
                b = hi
        else:
            raise CronError(f"bad cron field {text!r}")
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise CronError(f"{text!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, expr: str) -> Cron:
        parts = expr.split()
        if len(parts) != 5:
            raise CronError("a cron schedule has 5 fields: minute hour day month weekday")
        if parts[4] == "7":  # Sunday, spelled the other way
            parts[4] = "0"
        m, h, d, mo, w = (frozenset(_field(p, lo, hi))
                          for p, (lo, hi) in zip(parts, _FIELDS, strict=True))
        return cls(m, h, d, mo, w, any_day=parts[2] == "*", any_weekday=parts[4] == "*")

    def matches(self, t: _dt.datetime) -> bool:
        if t.minute not in self.minutes or t.hour not in self.hours or t.month not in self.months:
            return False
        dom, dow = t.day in self.days, (t.isoweekday() % 7) in self.weekdays
        if self.any_day or self.any_weekday:  # cron: both restricted means either may match
            return dom and dow
        return dom or dow

    def next_after(self, t: _dt.datetime) -> _dt.datetime | None:
        t = t.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
        for _ in range(366 * 24 * 60):  # a year of minutes at most
            if self.matches(t):
                return t
            t += _dt.timedelta(minutes=1)
        return None


def describe(expr: str) -> str:
    """'every 5 minutes', 'daily at 05:00', or the expression itself."""
    p = expr.split()
    if len(p) != 5:
        return expr
    m, h, dom, mon, dow = p
    if m.startswith("*/") and h == dom == mon == dow == "*":
        return f"every {m[2:]} minutes"
    if m.isdigit() and h.isdigit() and dom == mon == "*":
        at = f"{int(h):02d}:{int(m):02d}"
        if dow == "*":
            return f"daily at {at}"
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        if dow.isdigit() and int(dow) < 7:
            return f"{names[int(dow)]}s at {at}"
    return expr


Task = Callable[[threading.Event], dict[str, Any]]


@dataclass
class _State:
    running: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    last_run: str | None = None
    last_result: dict[str, Any] | None = None
    last_error: str | None = None
    last_tick: _dt.datetime | None = None


class Scheduler:
    def __init__(self, tasks: dict[str, tuple[str, Task]],
                 get_schedule: Callable[[], dict[str, str]],
                 now: Callable[[], _dt.datetime] = _dt.datetime.now):
        """tasks: name -> (description, fn(cancel) -> result)."""
        self.tasks = tasks
        self.get_schedule = get_schedule
        self.now = now
        self.state = {n: _State() for n in tasks}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_minute: _dt.datetime | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="fanscut-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for st in self.state.values():
            st.cancel.set()
        if self._thread:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.wait(5.0):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the scheduler must keep going
                log.exception("scheduler tick failed")

    def tick(self) -> list[str]:
        """Start every task due this minute (once per minute); returns their names."""
        minute = self.now().replace(second=0, microsecond=0)
        if self._last_minute == minute:
            return []
        self._last_minute = minute
        started = []
        sched = self.get_schedule()
        for name in self.tasks:
            expr = sched.get(name, "")
            if not expr:
                continue
            try:
                due = Cron.parse(expr).matches(minute)
            except CronError:
                log.warning("schedule.%s: bad cron %r", name, expr)
                continue
            if due and self.run(name, background=True):
                started.append(name)
        return started

    def run(self, name: str, background: bool = True) -> bool:
        """Start a task now; False if it is already running."""
        if name not in self.tasks:
            raise KeyError(name)
        with self._lock:
            st = self.state[name]
            if st.running:
                return False
            st.running = True
            st.cancel = threading.Event()
        if background:
            threading.Thread(target=self._run, args=(name,), name=f"fanscut-task-{name}",
                             daemon=True).start()
        else:
            self._run(name)
        return True

    def _run(self, name: str) -> None:
        st = self.state[name]
        st.last_run = self.now().isoformat(timespec="seconds")
        try:
            st.last_result = self.tasks[name][1](st.cancel)
            st.last_error = None
            log.info("scheduled task %s finished", name)
        except Exception as exc:  # noqa: BLE001 - recorded, shown in the UI
            log.exception("scheduled task %s failed", name)
            st.last_error = str(exc) or exc.__class__.__name__
        finally:
            st.running = False

    def cancel(self, name: str) -> bool:
        st = self.state[name]
        if st.running:
            st.cancel.set()
        return st.running

    def describe(self) -> list[dict[str, Any]]:
        sched = self.get_schedule()
        out = []
        for name, (desc, _fn) in self.tasks.items():
            expr = sched.get(name, "")
            nxt = None
            if expr:
                try:
                    n = Cron.parse(expr).next_after(self.now())
                    nxt = n.isoformat(timespec="minutes") if n else None
                except CronError:
                    nxt = None
            st = self.state[name]
            out.append({"name": name, "description": desc, "cron": expr,
                        "when": describe(expr) if expr else "off", "next_run": nxt,
                        "running": st.running, "last_run": st.last_run,
                        "last_result": st.last_result, "last_error": st.last_error})
        return out

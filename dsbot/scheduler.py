"""APScheduler jobs for dsbot's recurring work."""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler

from dsbot.audit import log
from dsbot.digest import write_digest
from dsbot.orchestrator import run_cycle
from dsbot.selfreview import write_self_review


def _scheduled_job(name: str, action: Any) -> Any:
    try:
        return action()
    except Exception as error:
        log(
            "scheduler",
            "scheduled_job_error",
            job=name,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise


def _weekly_learn_propose() -> Any:
    from dsbot.learning.tuner import propose_priority_tuning

    return propose_priority_tuning()


def create_scheduler(timezone: Any = None) -> BlockingScheduler:
    """Create a blocking scheduler using the machine's local timezone by default."""

    options = {"timezone": timezone} if timezone is not None else {}
    scheduler = BlockingScheduler(**options)
    scheduler.add_job(
        lambda: _scheduled_job("run_cycle", run_cycle),
        "interval",
        minutes=5,
        id="dsbot_run_cycle",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _scheduled_job("daily_digest", write_digest),
        "cron",
        hour=8,
        minute=0,
        id="dsbot_daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _scheduled_job("weekly_self_review", write_self_review),
        "cron",
        day_of_week="sun",
        hour=18,
        minute=0,
        id="dsbot_weekly_self_review",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _scheduled_job("weekly_learn_propose", _weekly_learn_propose),
        "cron",
        day_of_week="sun",
        hour=18,
        minute=0,
        id="dsbot_weekly_learn_propose",
        replace_existing=True,
    )
    return scheduler


def start_scheduler(timezone: Any = None) -> None:
    """Run the configured schedule until interrupted."""

    create_scheduler(timezone).start()

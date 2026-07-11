from __future__ import annotations

import argparse
from pathlib import Path

from hunter.automation import AutomationJobRunner, AutomationScheduler, load_automation_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter")
    parser.add_argument("--config", default="configs/automation.yaml")
    sub = parser.add_subparsers(dest="command")
    automation = sub.add_parser("automation")
    automation_sub = automation.add_subparsers(dest="automation_command")
    automation_sub.add_parser("start")
    automation_sub.add_parser("status")
    automation_sub.add_parser("list-jobs")
    show_job = automation_sub.add_parser("show-job")
    show_job.add_argument("job")
    run_once = automation_sub.add_parser("run-once")
    run_once.add_argument("job")
    cancel = automation_sub.add_parser("cancel")
    cancel.add_argument("run_id")
    args = parser.parse_args(argv)
    if args.command != "automation":
        parser.print_help()
        return 1
    config = load_automation_config(Path(args.config))
    runner = AutomationJobRunner()
    scheduler = AutomationScheduler(config.jobs, runner)
    if args.automation_command == "list-jobs":
        for job in config.jobs:
            print(f"{job.job_id}\t{job.name}\t{'enabled' if job.enabled else 'disabled'}")
        return 0
    if args.automation_command == "show-job":
        job = _job(config.jobs, args.job)
        print(job)
        return 0
    if args.automation_command == "run-once":
        run = runner.run_once(_job(config.jobs, args.job))
        print(f"{run.automation_run_id}\t{run.status}")
        return 0 if run.status in {"succeeded", "partial"} else 2
    if args.automation_command == "start":
        scheduler.start()
        print("scheduler started")
        return 0
    if args.automation_command == "status":
        status = scheduler.status()
        print(f"jobs={len(status.jobs)} active_runs={len(status.active_runs)} events={len(status.events)}")
        return 0
    if args.automation_command == "cancel":
        print(f"cancel requested: {args.run_id}")
        return 0
    automation.print_help()
    return 1


def _job(jobs: tuple[object, ...], job_id: str):
    for job in jobs:
        if job.job_id == job_id:
            return job
    raise SystemExit(f"Unknown automation job: {job_id}")


if __name__ == "__main__":
    raise SystemExit(main())

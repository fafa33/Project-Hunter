from __future__ import annotations

import argparse
from pathlib import Path

from hunter.automation import AutomationJobRunner, AutomationScheduler, load_automation_config
from hunter.dashboard import DashboardDataProvider, HtmlDashboardRenderer, load_dashboard_config
from hunter.dashboard.exceptions import DashboardPersistenceError
from hunter.opportunity.ranking import rank_opportunities
from hunter.persistence.sql import SessionFactory, UnitOfWork, create_schema, create_sqlite_engine
from hunter.probability.ranking import rank_probability_assessments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter")
    parser.add_argument("--config", default="configs/automation.yaml")
    sub = parser.add_subparsers(dest="command")
    automation = sub.add_parser("automation")
    automation_sub = automation.add_subparsers(dest="automation_command")
    start = automation_sub.add_parser("start")
    start.add_argument("--max-iterations", type=int, default=1)
    automation_sub.add_parser("status")
    automation_sub.add_parser("list-jobs")
    show_job = automation_sub.add_parser("show-job")
    show_job.add_argument("job")
    run_once = automation_sub.add_parser("run-once")
    run_once.add_argument("job")
    cancel = automation_sub.add_parser("cancel")
    cancel.add_argument("run_id")
    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--dashboard-config", default="configs/dashboard.yaml")
    dashboard_sub = dashboard.add_subparsers(dest="dashboard_command")
    build_dashboard = dashboard_sub.add_parser("build")
    build_dashboard.add_argument("--output")
    build_dashboard.add_argument("--sqlite-path")
    rank = sub.add_parser("rank")
    rank.add_argument(
        "--sort",
        choices=("opportunity", "conviction", "probability", "robustness", "consensus"),
        default="opportunity",
    )
    args = parser.parse_args(argv)
    if args.command == "rank":
        if args.sort in {"probability", "robustness", "consensus"}:
            rank_probability_assessments((), sort=args.sort)
        else:
            rank_opportunities((), sort=args.sort)
        return 0
    if args.command == "dashboard":
        return _dashboard(args)
    if args.command != "automation":
        parser.print_help()
        return 1
    config = load_automation_config(Path(args.config))
    runner = AutomationJobRunner()
    scheduler = AutomationScheduler(config.jobs, runner, polling_interval_seconds=config.polling_interval_seconds)
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
        scheduler.run_loop(max_iterations=args.max_iterations)
        print("scheduler started")
        return 0
    if args.automation_command == "status":
        status = scheduler.status()
        print(f"jobs={len(status.jobs)} active_runs={len(status.active_runs)} events={len(status.events)}")
        return 0
    if args.automation_command == "cancel":
        try:
            run = runner.cancel_by_id(args.run_id, jobs=config.jobs)
        except LookupError as exc:
            print(str(exc))
            return 2
        print(f"{run.automation_run_id}\t{run.status}")
        return 0
    automation.print_help()
    return 1


def _dashboard(args: object) -> int:
    config = load_dashboard_config(Path(args.dashboard_config))
    sqlite_path = args.sqlite_path or config.sqlite_path
    if sqlite_path is None:
        print("Dashboard build requires --sqlite-path or dashboard sqlite_path")
        return 2
    output = Path(args.output or config.output_path)
    engine = create_sqlite_engine(sqlite_path)
    create_schema(engine)
    with UnitOfWork(SessionFactory(engine)) as uow:
        if uow.repositories is None:
            raise DashboardPersistenceError("UnitOfWork did not expose repositories")
        view = DashboardDataProvider(uow.repositories, config).build()
    output.write_text(HtmlDashboardRenderer().render(view))
    print(str(output))
    return 0


def _job(jobs: tuple[object, ...], job_id: str):
    for job in jobs:
        if job.job_id == job_id:
            return job
    raise SystemExit(f"Unknown automation job: {job_id}")


if __name__ == "__main__":
    raise SystemExit(main())

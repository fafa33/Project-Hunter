from __future__ import annotations

import argparse
from pathlib import Path

from hunter.automation import AutomationJobRunner, AutomationScheduler, load_automation_config
from hunter.committee import (
    InvestmentCommitteeEngine,
    load_investment_committee_config,
)
from hunter.committee.ranking import rank_investment_committee
from hunter.dashboard import DashboardDataProvider, HtmlDashboardRenderer, load_dashboard_config
from hunter.dashboard.exceptions import DashboardPersistenceError
from hunter.necessity.ranking import rank_necessity_assessments
from hunter.opportunity.ranking import rank_opportunities
from hunter.patterns.ranking import rank_pattern_assessments
from hunter.persistence.sql import SessionFactory, UnitOfWork, create_schema, create_sqlite_engine
from hunter.probability.ranking import rank_probability_assessments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter")
    parser.add_argument("--config", default="configs/automation.yaml")
    sub = parser.add_subparsers(dest="command")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("project_slug", nargs="?")
    discover = sub.add_parser("discover")
    discover.add_argument("project_slug", nargs="?")
    validate = sub.add_parser("validate")
    validate.add_argument("project_slug", nargs="?")
    whales = sub.add_parser("whales")
    whales.add_argument("project_slug", nargs="?")
    reports = sub.add_parser("reports")
    reports.add_argument("project_slug", nargs="?")
    backtesting = sub.add_parser("backtesting")
    backtesting.add_argument("project_slug", nargs="?")
    alerts = sub.add_parser("alerts")
    alerts.add_argument("project_slug", nargs="?")
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
    committee = sub.add_parser("committee")
    committee.add_argument("--committee-config", default="configs/investment_committee.yaml")
    committee_sub = committee.add_subparsers(dest="committee_command")
    committee_evaluate = committee_sub.add_parser("evaluate")
    committee_evaluate.add_argument("project_slug", nargs="?")
    committee_report = committee_sub.add_parser("report")
    committee_report.add_argument("project_slug", nargs="?")
    committee_sub.add_parser("ranking")
    committee_sub.add_parser("champion")
    committee_history = committee_sub.add_parser("history")
    committee_history.add_argument("project_slug", nargs="?")
    rank = sub.add_parser("rank")
    rank.add_argument(
        "--sort",
        choices=(
            "opportunity",
            "conviction",
            "probability",
            "robustness",
            "consensus",
            "similarity",
            "historical",
            "pattern",
            "necessity",
            "gap",
            "rotation",
            "dependency",
            "committee",
            "committee-confidence",
            "evidence-robustness",
            "thesis-fragility",
        ),
        default="opportunity",
    )
    args = parser.parse_args(argv)
    if args.command in {"analyze", "discover", "validate", "whales", "reports", "backtesting", "alerts"}:
        print(f"{args.command} validation command ready for {getattr(args, 'project_slug', None) or 'all projects'}")
        return 0
    if args.command == "rank":
        if args.sort in {"committee", "committee-confidence", "evidence-robustness", "thesis-fragility"}:
            rank_investment_committee((), sort=args.sort)
        elif args.sort in {"probability", "robustness", "consensus"}:
            rank_probability_assessments((), sort=args.sort)
        elif args.sort in {"similarity", "historical", "pattern"}:
            rank_pattern_assessments((), sort=args.sort)
        elif args.sort in {"necessity", "gap", "rotation", "dependency"}:
            rank_necessity_assessments((), sort=args.sort)
        else:
            rank_opportunities((), sort=args.sort)
        return 0
    if args.command == "dashboard":
        return _dashboard(args)
    if args.command == "committee":
        return _committee(args)
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


def _committee(args: object) -> int:
    load_investment_committee_config(Path(args.committee_config))
    command = getattr(args, "committee_command", None)
    project = getattr(args, "project_slug", None)
    if command == "evaluate":
        print(f"committee evaluation requested for {project or 'all projects'}")
        return 0
    if command == "report":
        print("No persisted committee assessment available")
        return 0
    if command == "ranking":
        InvestmentCommitteeEngine().select_champion(())
        print("No qualified candidate")
        return 0
    if command == "champion":
        snapshot, _ = InvestmentCommitteeEngine().select_champion(())
        print(snapshot.no_selection_reason or snapshot.selection_reason)
        return 0
    if command == "history":
        print(f"committee history for {project or 'all projects'}")
        return 0
    print("committee command required")
    return 1


def _job(jobs: tuple[object, ...], job_id: str):
    for job in jobs:
        if job.job_id == job_id:
            return job
    raise SystemExit(f"Unknown automation job: {job_id}")


if __name__ == "__main__":
    raise SystemExit(main())

"""Compare tau2 runs of the Frontend/Backend Agent (and optionally llm_agent).

    uv run python tau2-fba/fba_report.py \
        data/simulations/fba_paired_airline_<ts> \
        data/simulations/fba_backend_only_airline_<ts> \
        [data/simulations/<llm_agent_run>] \
        --out misc/prototypes/results/airline.md --csv-dir misc/prototypes/results/airline

Pure post-processing of results.json; safe to re-run at any time. Metric definitions:
../misc/prototypes/text-frontend-backend-agent-tau2-integration-plan.md section 8.
"""

import argparse
import json
import sys
from pathlib import Path

from tau2_fba import metrics


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("runs", nargs="+", help="Run directories or results.json files.")
    p.add_argument("--out", default=None, help="Write the Markdown report here.")
    p.add_argument("--json", default=None, help="Write all summaries as JSON here.")
    p.add_argument(
        "--csv-dir", default=None, help="Write one per-task CSV per run here."
    )
    args = p.parse_args(argv)

    summaries = []
    for run in args.runs:
        results = metrics.load(run)
        if not results.simulations:
            raise SystemExit(f"{run}: no simulations found; nothing to report.")
        summary = metrics.summarize(results, source=run)
        summaries.append(summary)
        if args.csv_dir:
            name = (
                f"{summary['arm']}_{summary['domain']}_{Path(run).resolve().name}.csv"
            )
            metrics.write_per_task_csv(results, Path(args.csv_dir) / name)

    report = metrics.render_markdown(summaries)
    print(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

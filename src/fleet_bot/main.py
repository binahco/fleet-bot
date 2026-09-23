from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml
from bot_base import Digest, JsonRunStore, Schedule, Task, run_tasks
from cache_ratelimit import CachedProvider, RateLimiter, RealClock, ThrottledProvider
from ci_pack import metrics
from ci_pack.lints import run_lints
from llm_client import CompletionRequest, CompletionResult, LlmClient, ReplayProvider, Span
from llm_client.providers.opencode_cli import OpenCodeCLI
from schema_validate import SchemaRegistry
from test_kit import EvalCase, EvalDataset, run

from .models import DailyDigest

DEFAULT_MODEL = "opencode/big-pickle"
SCHEMA_ID = "daily-digest-v1"
PROMPT_ID = "daily-digest-generator"
ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_PROMPT = PROMPT_DIR / "daily-digest-generator.md"
DEFAULT_DATASET = ROOT / "evals" / "daily-digest.jsonl"
STORE_PATH = ROOT / ".bot" / "state.json"
EVIDENCE_PATH = ROOT / "docs" / "metrics" / "evidence.html"
DAILY_EVERY_SECONDS = 86400.0


def load_prompt(path: Path) -> tuple[str, str, str, Path]:
    text = path.read_text()
    if not text.startswith("---"):
        raise SystemExit(f"{path}: falta frontmatter")
    _, frontmatter, body = text.split("---", 2)
    data = yaml.safe_load(frontmatter)
    eval_path = ROOT / data["eval"] if not Path(data["eval"]).is_absolute() else Path(data["eval"])
    return data["id"], data["version"], body.strip(), eval_path


def render_fleet_bot(prompt_id: str, prompt_version: str, variables: dict) -> list[dict]:
    """Prompt templado para `daily-digest-generator`; cualquier otro prompt pasa crudo."""
    if prompt_id == PROMPT_ID:
        _, _, body, _ = load_prompt(DEFAULT_PROMPT)
        system_part = body.split("## Sistema\n", 1)[1].split("## Usuario\n", 1)[0].strip()
        user_part = body.split("## Usuario\n", 1)[1].strip().format(**variables)
        return [
            {"role": "system", "content": system_part},
            {"role": "user", "content": user_part},
        ]
    payload = {"prompt_id": prompt_id, "prompt_version": prompt_version, "variables": variables}
    return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def build_emitter(span_file: Path | None):
    def emit(span: Span, _result) -> None:
        if span_file is not None:
            with span_file.open("a") as handle:
                handle.write(span.as_jsonl() + "\n")
        else:
            import sys

            sys.stderr.write(span.as_jsonl() + "\n")

    return emit


def build_client(inner_provider, *, clock=None, span_file=None) -> dict:
    clock = RealClock() if clock is None else clock
    cached = CachedProvider(inner_provider, ttl_seconds=DAILY_EVERY_SECONDS, clock=clock)
    limiter = RateLimiter(rpm=120, burst=20, clock=clock)
    provider = ThrottledProvider(cached, limiter)
    registry = SchemaRegistry()
    registry.register(SCHEMA_ID, DailyDigest)
    client = LlmClient(
        provider,
        consumer_repo="fleet-bot",
        model_aliases={"fast": DEFAULT_MODEL},
        renderer=render_fleet_bot,
        validator=registry.make_validator(SCHEMA_ID),
        emitter=build_emitter(span_file),
    )
    return {"client": client, "cached": cached, "limiter": limiter}


def make_audit_task(repos: list[Path]) -> Task:
    def run_audit() -> str:
        violations: list[str] = []
        for repo in repos:
            report = run_lints([Path(repo)])
            violations.extend(str(v) for v in report.violations)
        if violations:
            head = violations[:3]
            raise RuntimeError(f"{len(violations)} violacion(es): " + "; ".join(head))
        return f"{len(repos)} repos, lints OK"

    return Task("audit", Schedule(every_seconds=DAILY_EVERY_SECONDS), run=run_audit)


def make_evidence_task(repos: list[Path], out_path: Path) -> Task:
    def run_evidence() -> str:
        report = metrics.collect([Path(r) for r in repos])
        html = metrics.render(report)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        return f"{report.repo_count} consumidores, ${report.total_cost_usd:.4f} acumulados"

    return Task("evidence", Schedule(every_seconds=DAILY_EVERY_SECONDS), run=run_evidence)


def make_digest_task(client: LlmClient, n_repos: int) -> Task:
    def run_digest() -> str:
        result = client.complete(
            CompletionRequest(
                prompt_id=PROMPT_ID,
                prompt_version="0.1.0",
                variables={
                    "date": date.today().isoformat(),
                    "facts": f"{n_repos} repos de la flota · audit + evidencia del día",
                },
                model_alias="fast",
                response_schema=SCHEMA_ID,
                tags=["fleet-bot", "week-9"],
            )
        )
        if not result.validation.ok:
            raise RuntimeError("; ".join(result.validation.errors))
        parsed = result.parsed
        return f"{parsed.summary} (watch: {', '.join(parsed.watch) or '—'})"

    return Task("digest", Schedule(every_seconds=DAILY_EVERY_SECONDS), run=run_digest)


def run_fleet_bot(
    repos: list[Path],
    *,
    inner_provider=None,
    store: Path = STORE_PATH,
    out_path: Path = EVIDENCE_PATH,
    clock=None,
    span_file: Path | None = None,
) -> Digest:
    built = build_client(inner_provider if inner_provider is not None else OpenCodeCLI(DEFAULT_MODEL), clock=clock, span_file=span_file)
    store_obj = JsonRunStore(store)
    tasks = [
        make_audit_task(repos),
        make_evidence_task(repos, out_path),
        make_digest_task(built["client"], len(repos)),
    ]
    return run_tasks(tasks, store=store_obj, clock=clock)


def judge_for(client: LlmClient) -> Callable[[EvalCase], CompletionResult]:
    def judge(case: EvalCase) -> CompletionResult:
        return client.complete(
            CompletionRequest(
                prompt_id=case.prompt_id,
                prompt_version=case.prompt_version,
                variables=case.input,
                model_alias="fast",
                response_schema=SCHEMA_ID,
                tags=["fleet-bot", "week-9"],
            )
        )

    return judge


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="fleet-bot: ciclo matutino de la flota (semana 9).")
    parser.add_argument("--repos", nargs="+", help="rutas de los repos consumidores a auditar")
    parser.add_argument("--store", default=str(STORE_PATH), help="archivo de estado de recurrencia")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "record", "replay-check"],
        help="run ejecuta el ciclo; record graba la cinta; replay-check valida sin LLM (D4).",
    )
    args = parser.parse_args(argv)
    repos = [Path(r) for r in (args.repos or [])]

    if args.command == "replay-check":
        dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
        provider = ReplayProvider(ROOT / "cassettes", record=False)
        client = build_client(provider)["client"]
        report = run(dataset, judge_for(client), mode="full", threshold=1.0)
        print(f"replay: pass={report.passed}/{report.total} threshold_ok={report.threshold_ok}")
        raise SystemExit(0 if report.threshold_ok else 1)

    if args.command == "record":
        dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
        provider = ReplayProvider(ROOT / "cassettes", record=True, inner=OpenCodeCLI(DEFAULT_MODEL))
        client = build_client(provider)["client"]
        report = run(dataset, judge_for(client), mode="full", threshold=1.0)
        print(f"grabadas {len(dataset.cases)} respuestas; pass={report.passed}/{report.total}")
        raise SystemExit(0 if report.threshold_ok else 1)

    digest = run_fleet_bot(repos, store=Path(args.store))
    print(digest.text())
    raise SystemExit(1 if digest.failed else 0)


if __name__ == "__main__":
    main()
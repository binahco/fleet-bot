from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bot_base import JsonRunStore
from fleet_bot.main import DEFAULT_DATASET, build_client, judge_for, run_fleet_bot
from llm_client.provider import ProviderRequest, ProviderResponse
from test_kit import EvalDataset, run as run_evalset

STUB_TEXT = '{"date": "2026-09-30", "summary": "flota en verde hoy", "tasks": [], "watch": []}'


class FakeClock:
    def __init__(self, tick: float = 0.0, wall: datetime | None = None) -> None:
        self.tick = tick
        self.wall = wall or datetime(2026, 9, 30, 8, 0, 0)

    def monotonic(self) -> float:
        return self.tick

    def sleep(self, seconds: float) -> None:
        self.tick += seconds

    def now(self) -> datetime:
        return self.wall


class StubProvider:
    name = "stub"

    def __init__(self, text: str | None = None) -> None:
        self.text = text or STUB_TEXT
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(text=self.text, model=request.model)

    def stream(self, request: ProviderRequest) -> list[str]:
        self.calls += 1
        return [self.text]


def mini_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    (repo / "prompts").mkdir(parents=True)
    (repo / "core-consumer.yml").write_text(
        f"week: 9\nname: {name}\ncore_version: 0.9.0\nreused_modules: [llm-client]\n"
        "estimated_new_surface: 20\n"
    )
    (repo / "prompts" / "demo.md").write_text(
        "---\nid: demo\nversion: 0.1.0\nschema: demo-v1\neval: evals/demo.jsonl\n---\n# demo\n"
    )
    (repo / "evals").mkdir(exist_ok=True)
    (repo / "evals" / "demo.jsonl").write_text("{}\n")
    return repo


def test_el_ciclo_completo_corre_y_devuelve_digest(tmp_path) -> None:
    repos = [mini_repo(tmp_path, "algo")]
    clock = FakeClock()
    stub = StubProvider()
    out = tmp_path / "docs" / "metrics" / "evidence.html"
    digest = run_fleet_bot(repos, inner_provider=stub, store=tmp_path / "state.json", out_path=out, clock=clock)
    assert [r.status for r in digest.results] == ["ok", "ok", "ok"]
    assert "flota en verde hoy" in digest.text()
    assert out.exists()


def test_recurrencia_no_duplica_ejecucion_del_mismo_dia(tmp_path) -> None:
    repos = [mini_repo(tmp_path, "algo")]
    clock = FakeClock()
    stub = StubProvider()
    store = tmp_path / "state.json"
    first = run_fleet_bot(repos, inner_provider=stub, store=store, clock=clock)
    second = run_fleet_bot(repos, inner_provider=stub, store=store, clock=clock)
    assert [r.status for r in first.results] == ["ok", "ok", "ok"]
    assert [r.status for r in second.results] == ["skipped", "skipped", "skipped"]
    assert stub.calls == 1


def test_digest_salida_invalida_falla_y_no_marca_done(tmp_path) -> None:
    clock = FakeClock()
    stub = StubProvider(text="no es json")
    digest = run_fleet_bot([mini_repo(tmp_path, "algo")], inner_provider=stub, store=tmp_path / "state.json", clock=clock)
    assert [r.status for r in digest.results][2] == "failed"
    state = JsonRunStore(tmp_path / "state.json")
    assert state.last_run("digest") is None


def test_el_judge_evoluciona_el_dataset_completo(tmp_path) -> None:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    stub = StubProvider()
    client = build_client(stub)["client"]
    out = run_evalset(dataset, judge_for(client), mode="full", threshold=1.0)
    assert out.total == 3
    assert out.passed == 3
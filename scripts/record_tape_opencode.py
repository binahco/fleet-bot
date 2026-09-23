from pathlib import Path

from fleet_bot.main import DEFAULT_DATASET, build_client, judge_for, load_prompt
from llm_client import ReplayProvider
from llm_client.providers.opencode_cli import OpenCodeCLI
from test_kit import EvalDataset, run

MODEL = "opencode/big-pickle"
ROOT = Path(__file__).resolve().parents[1]
CASSETTES = ROOT / "cassettes"


def main() -> None:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    prompt_id, prompt_version, _, _ = load_prompt(ROOT / "prompts" / "daily-digest-generator.md")
    assert dataset.prompt_id == prompt_id and dataset.prompt_version == prompt_version

    recorder = ReplayProvider(CASSETTES, record=True, inner=OpenCodeCLI(MODEL))
    client = build_client(recorder)["client"]

    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    print(f"grabadas {len(dataset.cases)} respuestas; pass={report.passed}/{report.total}")
    for case_report in report.cases:
        print(case_report.model_dump())


if __name__ == "__main__":
    main()
"""Score the pipeline on the checked test clips.

Runs every checked clip through the same pipeline as live.py (the whole clip
transcribed at once, then translated by Claude; several clips in parallel),
then:
- compares the Georgian Google heard with yours (word and character error rate),
- has Claude compare the English with your translation and list meaning errors,
- reports how long the English takes and the cost per hour of speech.

Results are saved to results/<date>_<model>.md and .json.

Usage:
    python tools/score.py
    python tools/score.py --model claude-haiku-4-5
    python tools/score.py --clips clip001 clip004
    python tools/score.py --streaming   # compare: the old live streaming mode
"""
import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic

from natro_voice import testset
from natro_voice.config import ROOT, load_env, load_wordlist
from natro_voice.pipeline import Session
from natro_voice.speech import AudioFile, Recognizer
from natro_voice.translate import DEFAULT_MODEL, MODELS, Translator, ask

RESULTS_DIR = ROOT / "results"
JUDGE_MODEL = "claude-opus-5-5"

JUDGE_SYSTEM = """You check a machine translation of Georgian speech against the speaker's own English translation (the reference). The speaker mixes English words into Georgian and often gives instructions to an AI assistant, which will act on the machine translation.

List every place where the machine English means something different from the reference. Ignore differences in wording, punctuation, capitalization and style when the meaning is the same, and differences in sentence boundaries.
- major: changes what the speaker meant, or would make an assistant do the wrong thing: a wrong action, object, name, title, number, time or negation; a missing or invented instruction or fact; a command translated as a statement or the other way round.
- minor: a small loss of nuance or tone, or missing or added filler.

Return an empty list if the meaning matches."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["major", "minor"]},
                    "kind": {"type": "string"},
                    "reference": {"type": "string", "description": "what the speaker meant, from the reference"},
                    "machine": {"type": "string", "description": "what the machine has instead, or empty if missing"},
                },
                "required": ["severity", "kind", "reference", "machine"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["errors"],
    "additionalProperties": False,
}


def words(text):
    """Lowercase words without punctuation, for error rates."""
    return re.sub(r"[^\w\s]", " ", text.casefold().replace("-", " ")).split()


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def error_rates(reference, heard):
    ref_words, heard_words = words(reference), words(heard)
    ref_chars, heard_chars = " ".join(ref_words), " ".join(heard_words)
    wer = edit_distance(ref_words, heard_words) / max(len(ref_words), 1)
    cer = edit_distance(ref_chars, heard_chars) / max(len(ref_chars), 1)
    return wer, cer


def judge(client, reference_ka, reference_en, machine_en):
    content = (f"<georgian>{reference_ka}</georgian>\n"
               f"<reference_english>{reference_en}</reference_english>\n"
               f"<machine_english>{machine_en}</machine_english>")
    text, cost = ask(client, JUDGE_MODEL, JUDGE_SYSTEM, [{"role": "user", "content": content}],
                     output_format={"type": "json_schema", "schema": JUDGE_SCHEMA}, max_tokens=8000)
    return json.loads(text)["errors"], cost


def score_clip(clip_id, args, terms, client):
    recognizer = Recognizer(languages=("ka-GE",) if args.georgian_only else ("ka-GE", "en-US"))
    translator = Translator(model=args.model, terms=terms)
    if args.oneshot:
        # Like live.py: the whole clip at once after the speaker finished; delay is measured from the end.
        start = time.monotonic()
        heard_ka, as_georgian = recognizer.transcribe(testset.read_pcm(clip_id))
        machine_en = translator.translate(heard_ka, as_georgian=as_georgian) if heard_ka else ""
        latencies = [time.monotonic() - start]
        heard_ka = as_georgian or heard_ka
    else:
        sentences = []
        session = Session(AudioFile(testset.path(clip_id, ".wav")), recognizer, translator,
                          on_result=lambda ka, en: sentences.append((ka, en)))
        session.start()
        session.finish()
        latencies = session.latencies
        heard_ka = " ".join(ka for ka, _ in sentences)
        machine_en = " ".join(en for _, en in sentences)

    reference_ka, reference_en = testset.read_text(clip_id, ".ka.txt"), testset.read_text(clip_id, ".en.txt")
    wer, cer = error_rates(reference_ka, heard_ka)
    errors, judge_cost = judge(client, reference_ka, reference_en, machine_en)

    return {
        "clip": clip_id,
        "seconds": testset.duration(clip_id),
        "reference_ka": reference_ka, "heard_ka": heard_ka,
        "reference_en": reference_en, "machine_en": machine_en,
        "wer": wer, "cer": cer,
        "errors": errors,
        "latencies": latencies,
        "google_cost": recognizer.cost, "claude_cost": translator.cost, "judge_cost": judge_cost,
    }


def count(errors, severity):
    return sum(e["severity"] == severity for e in errors)


def summarize(results, args):
    hours = sum(r["seconds"] for r in results) / 3600
    latencies = sorted(l for r in results for l in r["latencies"])
    google, claude = sum(r["google_cost"] for r in results), sum(r["claude_cost"] for r in results)
    words_total = sum(len(words(r["reference_ka"])) for r in results)
    major = sum(count(r["errors"], "major") for r in results)
    return {
        "model": args.model, "georgian_only": args.georgian_only, "oneshot": args.oneshot,
        "clips": len(results), "minutes": hours * 60,
        "clips_without_major_errors": sum(count(r["errors"], "major") == 0 for r in results),
        "major_errors": major,
        "minor_errors": sum(count(r["errors"], "minor") for r in results),
        "major_per_10_min": major / (hours * 6) if hours else 0,
        "wer": sum(r["wer"] * len(words(r["reference_ka"])) for r in results) / max(words_total, 1),
        "cer": statistics.mean(r["cer"] for r in results),
        "latency_median": statistics.median(latencies) if latencies else None,
        "latency_p90": latencies[int(len(latencies) * 0.9)] if latencies else None,
        "cost_per_hour_google": google / hours if hours else 0,
        "cost_per_hour_claude": claude / hours if hours else 0,
        "run_cost": google + claude + sum(r["judge_cost"] for r in results),
    }


def report(summary, results):
    s = summary
    lines = [
        f"# Score: {s['model']}{' (Georgian only)' if s['georgian_only'] else ''}"
        f"{', one-shot' if s['oneshot'] else ', streaming'}, {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- **Clips:** {s['clips']} ({s['minutes']:.1f} min)",
        f"- **English:** {s['clips_without_major_errors']}/{s['clips']} clips with no major error; "
        f"{s['major_errors']} major, {s['minor_errors']} minor errors ({s['major_per_10_min']:.1f} major per 10 min of speech)",
        f"- **Georgian heard by Google:** {s['wer']:.0%} word error rate, {s['cer']:.0%} character error rate",
    ]
    if s["latency_median"] is not None:
        after = "the clip ends" if s["oneshot"] else "you stop talking"
        lines.append(f"- **English ready:** {s['latency_median']:.1f} s after {after} (90% within {s['latency_p90']:.1f} s)")
    lines += [
        f"- **Cost per hour of speech:** ${s['cost_per_hour_google'] + s['cost_per_hour_claude']:.2f} "
        f"(Google ${s['cost_per_hour_google']:.2f}, Claude ${s['cost_per_hour_claude']:.2f})",
        f"- **This scoring run cost:** ${s['run_cost']:.2f}",
        "",
        "## Clips",
        "",
        "| Clip | Length | Major | Minor | Georgian word errors |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['clip']} | {r['seconds']:.0f} s | {count(r['errors'], 'major')} | "
                     f"{count(r['errors'], 'minor')} | {r['wer']:.0%} |")

    lines += ["", "## Errors", ""]
    for r in results:
        for e in sorted(r["errors"], key=lambda e: e["severity"]):
            lines.append(f"- **{r['clip']}**, {e['severity']}, {e['kind']}: "
                         f"meant \"{e['reference']}\", got \"{e['machine']}\"")

    lines += ["", "## Outputs", ""]
    for r in results:
        lines += [
            f"### {r['clip']}", "",
            f"- **Georgian (yours):** {r['reference_ka']}",
            f"- **Georgian (heard):** {r['heard_ka']}",
            f"- **English (yours):** {r['reference_en']}",
            f"- **English (machine):** {r['machine_en']}", "",
        ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    parser.add_argument("--georgian-only", action="store_true", help="recognizer expects Georgian only")
    parser.add_argument("--clips", nargs="*", help="score only these clips")
    parser.add_argument("--parallel", type=int, default=6, help="clips to run at once")
    parser.add_argument("--streaming", action="store_true",
                        help="use live streaming recognition, sentence by sentence (the old mode), for comparison")
    args = parser.parse_args()
    args.oneshot = not args.streaming

    sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    clip_ids = args.clips or [c for c in testset.clip_ids() if testset.state(c) == "checked"]
    if not clip_ids:
        print("No checked clips yet. Add some with tools/testset.py.")
        return

    minutes = sum(testset.duration(c) for c in clip_ids) / 60
    print(f"Scoring {len(clip_ids)} clips ({minutes:.1f} min of audio) with {args.model}...")
    terms, client = load_wordlist(), anthropic.Anthropic()
    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(score_clip, c, args, terms, client): c for c in clip_ids}
        for future in as_completed(futures):
            try:
                r = future.result()
            except Exception as e:
                print(f"  {futures[future]}: failed: {e}")
                continue
            results.append(r)
            print(f"  {r['clip']}: {count(r['errors'], 'major')} major, {count(r['errors'], 'minor')} minor errors")
    if not results:
        return
    results.sort(key=lambda r: r["clip"])

    summary = summarize(results, args)
    RESULTS_DIR.mkdir(exist_ok=True)
    stem = (f"{datetime.now():%Y-%m-%d_%H%M}_{args.model}{'_streaming' if args.streaming else ''}"
            f"{'_georgian-only' if args.georgian_only else ''}")
    (RESULTS_DIR / f"{stem}.md").write_text(report(summary, results), encoding="utf-8")
    (RESULTS_DIR / f"{stem}.json").write_text(
        json.dumps({"summary": summary, "clips": results}, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n" + report(summary, results).split("\n## Clips")[0].strip())
    print(f"\nFull report: results/{stem}.md")


if __name__ == "__main__":
    main()

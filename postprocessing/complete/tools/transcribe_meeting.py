"""Transcribe a meeting recording locally with faster-whisper.

This helper writes timestamped Markdown, plain text, and machine-readable JSON.
It is deliberately separate from the thesis analysis pipeline so the recording
is never treated as executable input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def stamp(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="small.en")
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type="int8",
        cpu_threads=args.threads,
        num_workers=1,
    )
    segments_iter, info = model.transcribe(
        str(args.audio),
        language="en",
        beam_size=5,
        best_of=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=True,
        initial_prompt=(
            "A thesis supervision meeting between Maneesh and Vincenzo about "
            "Full891 satellite constellation simulation, California and India, "
            "post-processing, contact networks (CN), task completion, latency, "
            "robustness, failure scenarios, Pareto analysis, and thesis results."
        ),
    )

    segments = []
    for segment in segments_iter:
        row = {
            "start": float(segment.start),
            "end": float(segment.end),
            "text": segment.text.strip(),
        }
        segments.append(row)
        print(f"[{stamp(row['start'])}–{stamp(row['end'])}] {row['text']}", flush=True)

    base = args.output_dir / "Vincenzo_meeting_transcript"
    metadata = {
        "audio_file": str(args.audio.resolve()),
        "model": args.model,
        "language": info.language,
        "language_probability": info.language_probability,
        "duration_seconds": info.duration,
        "duration_after_vad_seconds": info.duration_after_vad,
        "segments": segments,
    }
    (base.with_suffix(".json")).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (base.with_suffix(".txt")).write_text(
        "\n".join(f"[{stamp(x['start'])}–{stamp(x['end'])}] {x['text']}" for x in segments)
        + "\n",
        encoding="utf-8",
    )
    (base.with_suffix(".md")).write_text(
        "# Vincenzo meeting — automated transcript\n\n"
        "> Generated locally with faster-whisper. Timestamps are approximate; "
        "technical terms and speaker attribution require human verification.\n\n"
        + "\n\n".join(
            f"**{stamp(x['start'])}–{stamp(x['end'])}**  \n{x['text']}" for x in segments
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Stream Highlight Pipeline
─────────────────────────
1. Reads manual_cuts.json
2. Cuts clips with ffmpeg
3. Copies clips into remotion/public/clips/
4. Writes clips_manifest.json for Remotion
5. Calls Remotion to render final highlight reel

Usage:
  python auto_editor.py <video_file>
  python auto_editor.py <video_file> --precise
  python auto_editor.py <video_file> --no-remotion
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
from pathlib import Path

FPS = 30  # must match remotion/src/Root.tsx

def banner(text): print(f"\n── {text} {'─' * max(0, 46 - len(text))}")
def ok(text):     print(f"  [ok] {text}")
def warn(text):   print(f"  [!]  {text}")
def err(text):    print(f"  [x]  {text}")


# ─────────────────────────────────────────────────
# STEP 0: CHECKS
# ─────────────────────────────────────────────────

def check_deps():
    if shutil.which("ffmpeg") is None:
        print("[error] ffmpeg not found.")
        print("  macOS:   brew install ffmpeg")
        print("  Ubuntu:  sudo apt install ffmpeg")
        print("  Windows: https://ffmpeg.org/download.html")
        sys.exit(1)


# ─────────────────────────────────────────────────
# STEP 1: CUT CLIPS
# ─────────────────────────────────────────────────

def cut_clip(video_path, start, end, out_path, precise=False):
    duration = end - start
    if precise:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(out_path)
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err(f"ffmpeg failed on {out_path.name}")
        for line in [l for l in result.stderr.splitlines() if l.strip()][-3:]:
            print(f"       {line}")
        return False
    return True


def cut_all_clips(video_path, cuts, out_dir, precise):
    clip_paths = []
    for cut in cuts:
        idx       = cut["clip"]
        start     = cut["start_seconds"]
        end       = cut["end_seconds"]
        clip_path = out_dir / f"clip_{idx:02d}.mp4"

        print(f"  [{idx:02d}/{len(cuts)}]  {cut['start']} → {cut['end']}"
              f"  ({cut['duration_seconds']:.0f}s)", end="  ")

        if clip_path.exists():
            print("already exists, skipping.")
            clip_paths.append((clip_path, cut))
            continue

        if cut_clip(video_path, start, end, clip_path, precise):
            print(f"→ {clip_path.name}")
            clip_paths.append((clip_path, cut))
        else:
            print("FAILED — skipped.")

    return clip_paths


# ─────────────────────────────────────────────────
# STEP 2: COPY TO REMOTION + WRITE MANIFEST
# ─────────────────────────────────────────────────

def prepare_remotion(clip_pairs, out_dir, remotion_dir):
    """
    Copies each clip into remotion/public/clips/ so Remotion
    can reference them as staticFile('clips/clip_01.mp4').
    Writes clips_manifest.json that Remotion reads as --props.
    """
    public_clips = remotion_dir / "public" / "clips"
    public_clips.mkdir(parents=True, exist_ok=True)

    clips_data = []
    for clip_path, cut in clip_pairs:
        dest = public_clips / clip_path.name
        if not dest.exists():
            shutil.copy2(clip_path, dest)
            ok(f"Copied {clip_path.name} → remotion/public/clips/")
        else:
            print(f"  {clip_path.name} already in remotion/public/clips/")

        clips_data.append({
            "index":          cut["clip"],
            "clip_file":      clip_path.name,
            "duration_frames": round(cut["duration_seconds"] * FPS),
            "start_timecode": cut["start"],
        })

    manifest = {"clips": clips_data}
    manifest_path = out_dir / "clips_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ok(f"Manifest written → {manifest_path.name}")
    return manifest_path


# ─────────────────────────────────────────────────
# STEP 3: REMOTION RENDER
# ─────────────────────────────────────────────────

def call_remotion(manifest_path, out_dir, remotion_dir):
    if not remotion_dir.exists():
        warn(f"Remotion project not found at {remotion_dir}")
        return False

    if shutil.which("npx") is None:
        warn("npx not found. Install Node.js from https://nodejs.org")
        return False

    final_path = out_dir / "final_highlights.mp4"

    cmd = [
    "npx", "remotion", "render",
    "src/index.ts",
    "HighlightReel",
    str(final_path),
    f"--props={str(manifest_path)}",
    "--browser-executable=C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ]

    print(f"  Rendering... this will take a few minutes.")
    result = subprocess.run(
        cmd,
        cwd=str(remotion_dir),
        shell=True,
        capture_output=False
    )
    if result.returncode == 0:
        ok(f"Done → {final_path}")
        return True
    else:
        err("Remotion render failed.")
        return False


# ─────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stream Highlight Pipeline")
    parser.add_argument("video",         help="Path to source video file")
    parser.add_argument("--cuts",        help="Path to manual_cuts.json (default: next to video)")
    parser.add_argument("--output",      help="Output directory (default: highlights/ next to video)")
    parser.add_argument("--remotion-dir",help="Remotion project folder (default: remotion/ next to this script)")
    parser.add_argument("--precise",     action="store_true", help="Frame-accurate cuts via re-encode (slower)")
    parser.add_argument("--no-remotion", action="store_true", help="Skip Remotion render")
    args = parser.parse_args()

    check_deps()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"[error] Video not found: {video_path}"); sys.exit(1)

    cuts_path = Path(args.cuts) if args.cuts else video_path.parent / "manual_cuts.json"
    if not cuts_path.exists():
        print(f"[error] manual_cuts.json not found at {cuts_path}")
        print("  Export it from stream_logger.html first."); sys.exit(1)

    cuts = json.loads(cuts_path.read_text())
    if not cuts:
        print("[error] manual_cuts.json is empty."); sys.exit(1)

    out_dir = Path(args.output).resolve() if args.output else video_path.parent / "highlights"
    out_dir.mkdir(parents=True, exist_ok=True)

    script_dir   = Path(__file__).parent
    remotion_dir = Path(args.remotion_dir).resolve() if args.remotion_dir else script_dir / "remotion"

    print(f"\n  Stream Highlight Pipeline")
    print(f"  Source  : {video_path.name}")
    print(f"  Cuts    : {len(cuts)} clips")
    print(f"  Output  : {out_dir}")
    print(f"  Mode    : {'precise (re-encode)' if args.precise else 'fast (copy)'}")

    banner("Step 1 of 3 — Cutting clips")
    clip_pairs = cut_all_clips(video_path, cuts, out_dir, args.precise)
    if not clip_pairs:
        print("\n[error] No clips cut."); sys.exit(1)
    ok(f"{len(clip_pairs)} clips ready.")

    banner("Step 2 of 3 — Preparing Remotion assets")
    manifest_path = prepare_remotion(clip_pairs, out_dir, remotion_dir)

    if not args.no_remotion:
        banner("Step 3 of 3 — Remotion render")
        call_remotion(manifest_path, out_dir, remotion_dir)
    else:
        print("\n── Step 3 skipped. When Remotion is set up run:")
        print(f"   python auto_editor.py {video_path.name}")

    print(f"\n{'─'*50}\nDone. {out_dir}\n{'─'*50}\n")

if __name__ == "__main__":
    main()
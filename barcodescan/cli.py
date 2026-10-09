import argparse
import os
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path

from .config import CHUNKSIZE, DEFAULT_DPIS, PROGRESS_EVERY, Settings
from .output import PageMerger, write_results
from .pipeline import init_worker, process_task
from .sources import build_tasks, collect_files


def parse_dpis(text):
    """'150,170,200' -> (150, 170, 200)"""
    return tuple(int(x) for x in text.split(",") if x.strip())


def fmt_time(seconds):
    """Format seconds as m:ss, or h:mm:ss for long runs."""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def run(input_path, output_path, workers, settings: Settings):
    """Scan one file (or zip, or folder) at every DPI in settings.dpis,
    and write one merged result file. Returns stats including elapsed time."""
    started = time.perf_counter()

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(input_path)
        if src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src) as z:
                z.extractall(tmp)
            src = Path(tmp)

        files = list(collect_files(src))
        if not files:
            print(f"No PDF or image files found in {input_path}", file=sys.stderr)
            return None

        pages = list(build_tasks(files))
        # Page-major order: page 1 at every DPI, then page 2 at every DPI, and so on.
        tasks = [(*t, dpi) for t in pages for dpi in settings.dpis]
        total = len(tasks)
        print(f"  {len(pages)} page(s) x {len(settings.dpis)} DPI pass(es) = {total} tasks",
              file=sys.stderr)

        merger = PageMerger()
        with ProcessPoolExecutor(workers, initializer=init_worker) as ex:
            for i, rec in enumerate(
                    ex.map(process_task, tasks, repeat(settings), chunksize=CHUNKSIZE), 1):
                merger.add(rec)
                if i % PROGRESS_EVERY == 0 or i == total:
                    elapsed = time.perf_counter() - started
                    print(f"\r  task {i}/{total}  elapsed {fmt_time(elapsed)}",
                          end="", file=sys.stderr, flush=True)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_results(output_path, merger)

    elapsed = time.perf_counter() - started
    stats = merger.summary()
    stats["seconds"] = elapsed

    print(f"\n  done in {fmt_time(elapsed)}: {stats['pages']} pages, "
          f"{stats['found_pages']} with barcodes, {stats['codes']} barcodes, "
          f"{stats['errors']} errors -> {output_path}", file=sys.stderr)
    print("  new barcodes per pass: " +
          ", ".join(f"{d} DPI: +{n}" for d, n in sorted(stats["new_by_dpi"].items())),
          file=sys.stderr)
    return stats


def run_folder(input_dir, output_dir, workers, settings: Settings, fmt):
    """Process every PDF/image in input_dir one at a time.
    input/a/b.pdf -> output/a/b.csv (or .json)."""
    batch_start = time.perf_counter()
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)

    targets = list(collect_files(input_dir))
    if not targets:
        sys.exit(f"No PDF or image files found in {input_dir}")

    print(f"Found {len(targets)} file(s) in {input_dir}", file=sys.stderr)
    summary = []
    for i, (path, name) in enumerate(targets, 1):
        out = output_dir / Path(name).with_suffix(f".{fmt}")
        print(f"\n[{i}/{len(targets)}] {name}", file=sys.stderr)
        try:
            stats = run(path, out, workers, settings)
            summary.append((name, "ok", stats))
        except Exception as e:
            print(f"  failed: {e}", file=sys.stderr)
            summary.append((name, f"failed: {e}", None))

    batch_elapsed = time.perf_counter() - batch_start
    print("\nSummary", file=sys.stderr)
    for name, status, stats in summary:
        if stats:
            print(f"  {name}: {stats['codes']} barcodes on {stats['found_pages']} "
                  f"of {stats['pages']} pages ({stats['errors']} errors)  "
                  f"time {fmt_time(stats['seconds'])}", file=sys.stderr)
        else:
            print(f"  {name}: {status}", file=sys.stderr)
    print(f"\nTotal time for {len(targets)} file(s): {fmt_time(batch_elapsed)}",
          file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Find and decode barcodes in PDFs and images.")
    ap.add_argument("input", help="PDF, image, zip, or a folder of them")
    ap.add_argument("-o", "--output",
                    help="output file (single input) or output folder (folder input)")
    ap.add_argument("--format", choices=["csv", "json"], default="csv",
                    help="result format for folder input (default csv)")
    ap.add_argument("--dpi", default=",".join(map(str, DEFAULT_DPIS)),
                    help="comma-separated render passes, run in order "
                         f"(default {','.join(map(str, DEFAULT_DPIS))})")
    ap.add_argument("-w", "--workers", type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--fallback", action="store_true",
                    help="run a full-page decode on pages with no hit (slow)")
    ap.add_argument("--debug", metavar="DIR", help="save detection overlays to DIR")
    a = ap.parse_args()

    dpis = parse_dpis(a.dpi)
    if not dpis:
        sys.exit("--dpi needs at least one value, e.g. --dpi 150,200,250")
    settings = Settings(dpis=dpis, fallback=a.fallback, debug_dir=a.debug)

    src = Path(a.input)
    if src.is_dir():
        out_dir = Path(a.output) if a.output else src.parent / f"{src.name}_results"
        run_folder(src, out_dir, a.workers, settings, a.format)
    else:
        out = a.output or str(src.with_suffix("")) + "_barcodes.csv"
        run(a.input, out, a.workers, settings)


if __name__ == "__main__":
    main()
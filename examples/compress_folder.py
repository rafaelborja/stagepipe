"""Compress every file in a folder to .gz, in parallel, with a hard limit on memory.

    python examples/compress_folder.py                 # makes a demo folder of files and compresses it
    python examples/compress_folder.py SRC_DIR OUT_DIR # your own files

read -> compress -> write run as three stages. Reading and writing wait on the disk, compressing
uses the CPU (zlib releases the GIL, so threads really run in parallel). `max_in_flight=6` means
at most 6 files are in memory at any moment, however large the folder is: that is the point of
the window. The last stage prints the report in file order.
"""
import gzip
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))   # not needed once installed
from stagepipe import Stage, run


def demo_files(folder: Path, count=24) -> None:
    rnd = random.Random(7)
    words = [f"word{n}" for n in range(500)]
    for k in range(count):
        size = rnd.randint(1, 5)                           # 1 to 5 "units" of text, so files differ
        text = " ".join(rnd.choice(words) + str(rnd.randrange(10_000)) for _ in range(size * 100_000))
        (folder / f"file_{k:02d}.txt").write_text(text, encoding="ascii")


def main(src: Path, out: Path) -> None:
    files = sorted(p for p in src.iterdir() if p.is_file())
    out.mkdir(parents=True, exist_ok=True)

    def read(path, i):
        return path, path.read_bytes()

    def compress(item, i):
        path, data = item
        return path, len(data), gzip.compress(data, 6)

    def write(item, i):
        path, raw, packed = item
        (out / (path.name + ".gz")).write_bytes(packed)
        return path.name, raw, len(packed)

    def report(item, i):                                   # one worker, in file order
        name, raw, packed = item
        print(f"  {name:14} {raw / 1e6:6.2f} MB -> {packed / 1e6:5.2f} MB  ({packed / raw:4.0%})")
        return item

    print(f"{len(files)} files from {src}\n")
    start = time.perf_counter()
    run(files, [Stage("read", read, workers=2), Stage("compress", compress, workers=4),
                Stage("write", write, workers=2), Stage("report", report, workers=1, ordered=True)],
        max_in_flight=6)
    fast = time.perf_counter() - start

    start = time.perf_counter()                            # the same work, one file at a time
    for path in files:
        data = path.read_bytes()
        (out / (path.name + ".seq.gz")).write_bytes(gzip.compress(data, 6))
    slow = time.perf_counter() - start
    print(f"\nstagepipe: {fast:.2f}s   one at a time: {slow:.2f}s   ({slow / fast:.1f}x faster)")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
    else:
        tmp = Path(tempfile.mkdtemp(prefix="stagepipe-demo-"))
        try:
            (tmp / "in").mkdir()
            print("making demo files ...")
            demo_files(tmp / "in")
            main(tmp / "in", tmp / "out")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

"""A pizza kitchen, to see what stagepipe does.

    python examples/pizza_kitchen.py

Eight orders go through four stations. The oven is the slow one, so it gets the most attention:
two ovens, and every pizza goes in the moment an oven is free. The boxing station has one worker
and boxes the pizzas in order number, even though they come out of the oven in any order.
Compare with a kitchen that finishes ALL the dough before ANY topping, and so on.
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))   # not needed once installed
from stagepipe import Stage, run

ORDERS = ["margherita", "pepperoni", "pineapple (sorry)", "four cheese", "veggie", "anchovy", "bbq chicken", "funghi"]
STATIONS = [("dough", 0.15, 2), ("toppings", 0.20, 2), ("oven", 0.40, 2), ("box", 0.03, 1)]   # name, seconds, staff

log, lock, t0 = [], threading.Lock(), [0.0]


def station(name, seconds):
    def work(pizza, i):
        start = time.perf_counter() - t0[0]
        time.sleep(seconds)
        with lock:
            log.append((name, i, start, time.perf_counter() - t0[0]))
        return pizza
    return work


def timeline(width=68):
    end = max(e for *_, e in log)
    print(f"\n  {'':9}0s{' ' * (width - 8)}{end:.1f}s")
    for name, _, _ in STATIONS:
        row = ["."] * width
        for n, i, s, e in log:
            if n == name:
                for x in range(int(s / end * (width - 1)), max(int(e / end * (width - 1)), int(s / end * (width - 1)) + 1)):
                    row[x] = str(i + 1)
        print(f"  {name:9}{''.join(row)}")
    print("            (digits are order numbers; a dot is an idle station)")


# 1. the stagepipe kitchen
print("Kitchen 1: every pizza moves on as soon as the next station is free\n")
t0[0] = time.perf_counter()
run(ORDERS,
    [Stage(n, station(n, s), workers=w, ordered=(n == "box")) for n, s, w in STATIONS],
    max_in_flight=6,
    on_done=lambda i, pizza: print(f"  {time.perf_counter() - t0[0]:4.2f}s  order #{i + 1} is out the door: {pizza}"))
fast = time.perf_counter() - t0[0]
timeline()

# 2. the same stations, but each station finishes ALL pizzas before the next one starts
print("\nKitchen 2: same staff, but every station waits for the whole batch\n")
batch, start = list(ORDERS), time.perf_counter()
for n, s, w in STATIONS:
    with ThreadPoolExecutor(w) as pool:
        batch = list(pool.map(lambda p, f=station(n, s): f(p, 0), batch))
slow = time.perf_counter() - start
print(f"  first pizza out the door after {slow:.2f}s (the whole batch)")

print(f"\nKitchen 1 finished in {fast:.2f}s, kitchen 2 in {slow:.2f}s: {slow / fast:.1f}x faster, "
      f"and the first pizza left after {min(e for n, _, _, e in log if n == 'box'):.2f}s instead of {slow:.2f}s.")

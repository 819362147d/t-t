import sys, time
sys.path.insert(0, "/home/hatch/workspace/t-t-recovery/recovery-kit-full-20261027/mexc")
sys.path.insert(0, "/home/hatch/workspace/mexc-extension")
from mexc_trader_daemon_v1_20x import Trader
t = Trader(None, None, dry_run=True)
t.capital = 1.67
round_t = int(time.time()*1000)
cands = [
    {"sym":"PYPLSTOCKUSDT","dir":"DOWN","prob":0.43,"close":54.94},
    {"sym":"TESLAUSDT","dir":"UP","prob":0.56,"close":394.4},
    {"sym":"SHEINSTOCKUSDT","dir":"DOWN","prob":0.42,"close":10.0},
    {"sym":"RUNEUSDT","dir":"UP","prob":0.56,"close":0.6212},
]
print("calling maybe_open dry-run")
t.maybe_open(round_t, cands)
print("done, see log")

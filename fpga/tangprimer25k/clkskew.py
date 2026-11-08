# nextpnr --post-route script for build.sh chip: the routed clock delay from the clk pin to
# every sink, grouped by cell type and port, written to $CLKSKEW_OUT. build.sh fails the build if
# the spread exceeds its limit (a clock on general routing showed 5.5 ns of skew and real hold
# failures; see README.md).
import collections
import os

net = ctx.nets["chip.clk"]
by = collections.defaultdict(list)
for u in net.users:
    by[u.cell.type + "." + u.port].append((ctx.getDelayNS(ctx.getNetinfoRouteDelay(net, u)), u.cell.name))
with open(os.environ["CLKSKEW_OUT"], "w") as f:
    for k, v in sorted(by.items()):
        v.sort()
        f.write("%s %d %.3f %.3f\n" % (k, len(v), v[0][0], v[-1][0]))

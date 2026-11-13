"""sdf_prep.py NETLIST SDF OUT_DIR: a netlist and SDF pair that Icarus can annotate (test/Makefile, SDF=).

The harden's SDF (OpenSTA) and Icarus 13's $sdf_annotate disagree in four places; each would end the
annotation early or abort the run, and each is changed here in copies of the two files only:

  rename  The harden flattens the design, so a cell instantiated inside a module keeps its RTL path
          as one escaped name: the clock-gate latches, the buffers on the gated clocks, the SRAM
          macros, e.g. `\\u_core.g_ctx[3].u_ctx.g_r0.g_l[0].l`. The SDF writes it with escaped dots
          (`u_core\\.g_ctx\\[3\\]...`); Icarus splits an SDF path at every dot, escaped or not, finds
          no submodule `u_core` in the flat netlist and aborts (vpi_scan on a NULL handle). Each
          escaped instance name with a character other than [A-Za-z0-9_$] becomes a plain
          identifier, in both files (each such character becomes `_`; a suffix if the result is
          taken). Only names change: every cell, net and connection is the netlist's own.
  bus     The SRAM models declare one vector path, (posedge A_CLK *> (A_DOUT : A_DIN)); OpenSTA
          writes one IOPATH per bit (A_CLK A_DOUT[0] ...). Icarus accepts only the whole bus, and a
          bit-selected IOPATH is a syntax error that stops the parser, so every CELL after the first
          SRAM gets no delays. The bits' IOPATHs become one per bus: for each delay its minimum
          over the bits as min and its maximum as typ and max (the slowest bit for every bit:
          pessimistic by the spread between bits, under 0.04 ns on the 8x4's macros).
  tcheck  TIMINGCHECK entries are dropped. Icarus does not apply timing checks (one warning per
          CELL), and the SRAM's bit-selected ones are the same syntax error as above. Setup and
          hold are signed off by STA; here a timing failure shows as a wrong value the tests see.
  header  VOLTAGE and TEMPERATURE with an empty typical field (1.200::1.200) get it from the
          others; Icarus selects the typical value and reports an error on an empty one. PROCESS
          is a quoted string ("1.000::1.000") that Icarus accepts; it stays as written.

The Makefile compiles with -ginterconnect: without it Icarus drops every INTERCONNECT (the wire
delays, 75,856 in the 8x4's SDF) with an error line each. With all four steps the chip's run must
print no SDF ERROR and no SDF WARNING (v17 campaign check 20). OUT_DIR gets gate_level_netlist.v
and the SDF under the input's file name.

python3 sdf_prep.py --self-test: a small design with each case (an escaped buffer, a wire, an
escaped two-bit SRAM-like cell with per-bit IOPATHs and a bit-selected SETUP, the header). With
every step, every delay lands at its time and Icarus prints no SDF error or warning; leaving out
any one step (or all) must fail.
"""
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

STEPS = ("rename", "bus", "tcheck", "header")
INST = re.compile(r"^(\s*)([A-Za-z_]\w*)(\s+)\\(\S+)(\s*\()", re.M)
IDENT = re.compile(r"(?<![\\\w$])[A-Za-z_][\w$]*")
BIT = re.compile(r"^(\s*)\(IOPATH\s+(\S+|\((?:posedge|negedge)\s+\S+\))\s+([A-Za-z_]\w*)\[(\d+)\]\s+"
                 r"((?:\([^()]*\)\s*)+)\)\s*$")


def plain(name):
    return re.fullmatch(r"[A-Za-z_][\w$]*", name) is not None


def rename_map(net):
    """{escaped instance name (without the backslash): its new plain name}."""
    taken = set(IDENT.findall(net))
    taken |= set(m.group(1) for m in re.finditer(r"\\(\S+)", net))
    out = {}
    for m in INST.finditer(net):
        old = m.group(4)
        if plain(old) or old in out:
            continue
        new = re.sub(r"[^\w$]", "_", old)
        if not re.match(r"[A-Za-z_]", new):
            new = "i_" + new
        base, k = new, 0
        while new in taken:
            k += 1
            new = f"{base}_sdf{k}"
        taken.add(new)
        out[old] = new
    return out


def prep_netlist(net, ren):
    return INST.sub(lambda m: m.group(1) + m.group(2) + m.group(3)
                    + (ren[m.group(4)] if m.group(4) in ren else "\\" + m.group(4))
                    + m.group(5), net)


def unesc(s):
    return re.sub(r"\\(.)", r"\1", s)


def split_path(tok):
    """An SDF port path inst.pin: the instance (unescaped) and the pin (as written)."""
    parts = re.split(r"(?<!\\)\.", tok)
    return unesc(".".join(parts[:-1])), parts[-1]


def do_rename(sdf, ren, n):
    def inst(m):
        name = unesc(m.group(2))
        if name in ren:
            n["instance"] += 1
            return m.group(1) + ren[name] + ")"
        return m.group(0)

    def path(tok):
        if "\\" not in tok:
            return tok
        i, pin = split_path(tok)
        if i in ren:
            n["path"] += 1
            return ren[i] + "." + pin
        return tok

    sdf = re.sub(r"(\(INSTANCE\s+)([^\s()]+)\)", inst, sdf)
    sdf = re.sub(r"(\(INTERCONNECT\s+)(\S+)(\s+)(\S+)",
                 lambda m: m.group(1) + path(m.group(2)) + m.group(3) + path(m.group(4)), sdf)
    # nothing may still name an escaped instance: an INSTANCE or an INTERCONNECT end with a
    # backslash in its instance part is a name this step does not know
    left = [m.group(2) for m in re.finditer(r"(\(INSTANCE\s+)([^\s()]+)\)", sdf) if "\\" in m.group(2)]
    for m in re.finditer(r"\(INTERCONNECT\s+(\S+)\s+(\S+)", sdf):
        left += [t for t in m.groups() if "\\" in split_path(t)[0]]
    if left:
        raise SystemExit(f"sdf_prep: {len(left)} SDF names not in the netlist's escaped instances, e.g. {left[:3]}")
    return sdf


def combine(groups):
    """Per-bit delay lists -> one: per value, min of the mins, max of the typs and of the maxes."""
    vals = [re.findall(r"\(([^()]*)\)", g) for g in groups]
    if len({len(v) for v in vals}) != 1:
        raise SystemExit(f"sdf_prep: per-bit IOPATHs with different delay counts: {groups[:2]}")
    out = []
    for k in range(len(vals[0])):
        trip = [v[k].strip() for v in vals if v[k].strip()]
        if not trip:
            out.append("()")
            continue
        f = [[float(x) for x in (t.split(":") if ":" in t else [t, t, t])] for t in trip]
        lo, ty, hi = min(x[0] for x in f), max(x[1] for x in f), max(x[2] for x in f)
        out.append(f"({lo:.3f}:{ty:.3f}:{hi:.3f})")
    return " ".join(out)


def do_bus(sdf, n):
    lines, out, i = sdf.split("\n"), [], 0
    while i < len(lines):
        m = BIT.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        ind, src, bus = m.group(1), m.group(2), m.group(3)
        groups = []
        while i < len(lines):
            m2 = BIT.match(lines[i])
            if not (m2 and m2.group(2) == src and m2.group(3) == bus):
                break
            groups.append(m2.group(5))
            i += 1
        out.append(f"{ind}(IOPATH {src} {bus} {combine(groups)})")
        n["bus"] += 1
        n["bits"] += len(groups)
    sdf = "\n".join(out)
    left = [ln.strip() for ln in sdf.split("\n") if "(IOPATH" in ln and "[" in ln]
    if left:
        raise SystemExit(f"sdf_prep: {len(left)} bit-selected IOPATHs this step does not handle, e.g. {left[:2]}")
    return sdf


def do_tcheck(sdf, n):
    out, pos = [], 0
    while True:
        i = sdf.find("(TIMINGCHECK", pos)
        if i < 0:
            break
        depth, j = 0, i
        while True:
            c = sdf[j]
            depth += (c == "(") - (c == ")")
            j += 1
            if depth == 0:
                break
        start = sdf.rfind("\n", 0, i) + 1          # the block's own lines, indentation included
        if sdf[start:i].strip():
            start = i
        end = sdf.find("\n", j)
        end = len(sdf) if end < 0 else (end if sdf[j:end].strip() else end + 1)
        out.append(sdf[pos:start])
        pos = end
        n["tcheck"] += 1
    out.append(sdf[pos:])
    return "".join(out)


def do_header(sdf, n):
    def fill(m):
        f = [x.strip() for x in m.group(2).split(":")]
        if '"' in m.group(2) or len(f) != 3 or all(f) or not any(f):   # a quoted PROCESS is a string
            return m.group(0)
        v = next(x for x in f if x)
        n["header"] += 1
        return f"({m.group(1)} {':'.join(x or v for x in f)})"
    return re.sub(r"\((VOLTAGE|PROCESS|TEMPERATURE)\s+([^()]*)\)", fill, sdf)


def prep_sdf(sdf, ren, steps=STEPS):
    n = dict.fromkeys(("instance", "path", "bus", "bits", "tcheck", "header"), 0)
    if "rename" in steps:
        sdf = do_rename(sdf, ren, n)
    if "tcheck" in steps:
        sdf = do_tcheck(sdf, n)
    if "bus" in steps:
        sdf = do_bus(sdf, n)
    if "header" in steps:
        sdf = do_header(sdf, n)
    return sdf, n


def main(netp, sdfp, outd, steps=STEPS):
    # cocotb's make reads the Makefile twice: the second read finds a stamp of the same inputs
    stamp = Path(outd) / (Path(sdfp).name + ".sha256")
    key = " ".join([hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (netp, sdfp, __file__)] + list(steps))
    if stamp.exists() and stamp.read_text() == key and all(
            (Path(outd) / f).exists() for f in ("gate_level_netlist.v", Path(sdfp).name)):
        print(f"sdf_prep: {outd} is up to date")
        return
    net = Path(netp).read_text()
    ren = rename_map(net) if "rename" in steps else {}
    sdf, n = prep_sdf(Path(sdfp).read_text(), ren, steps)
    out = Path(outd)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate_level_netlist.v").write_text(prep_netlist(net, ren))
    (out / Path(sdfp).name).write_text(sdf)
    stamp.write_text(key)
    print(f"sdf_prep: {len(ren)} escaped instance names renamed; SDF: {n['instance']} CELL instances, "
          f"{n['path']} INTERCONNECT ends; {n['bits']} per-bit IOPATHs -> {n['bus']} bus IOPATHs; "
          f"{n['tcheck']} TIMINGCHECKs dropped; {n['header']} header values filled -> {out}")


SELF_V = r"""`timescale 1ns / 1ps
module bufx (input A, output X);
  buf (X, A);
  specify (A => X) = (1.0, 1.0); endspecify
endmodule
module ramx (input C, input [1:0] D, output [1:0] Q);   // the SRAM models' kind of path
  reg [1:0] r = 2'b00;
  assign Q = r;
  always @(posedge C) r <= D;
  specify (posedge C *> (Q : D)) = (1.0, 1.0); endspecify
endmodule
module top (input a, input k, output x, output y, output [1:0] q, output z);
  wire m;
  bufx \u.g[0].b (.A(a), .X(m));
  bufx c (.A(m), .X(x));
  assign y = m;
  ramx \u.mem (.C(k), .D(2'b11), .Q(q));
  bufx w (.A(q[1]), .X(z));
endmodule
module tb;
  reg a = 0, k = 0; wire x, y, z; wire [1:0] q;
  top dut (.a(a), .k(k), .x(x), .y(y), .q(q), .z(z));
  task fail(input [8*40-1:0] what); begin $display("SELF FAIL: %0s", what); $finish; end endtask
  initial begin
    $sdf_annotate(`SDF, dut);
    #10 a = 1;
    // b's path 2.5 ns, the wire to c 0.4 ns, c's own 1.0 ns (the model's, not in the SDF)
    #2.4 if (y !== 1'b0) fail("IOPATH early");
    #0.2 if (y !== 1'b1) fail("IOPATH late");
    #1.1 if (x !== 1'b0) fail("INTERCONNECT not applied");
    #0.3 if (x !== 1'b1) fail("x late");
    // the SRAM-like cell: bits' paths 2.0 and 3.0 ns, so the bus takes 3.0; then a 0.3 ns wire
    // from q[1] to w, and w's own 1.0 ns
    #6.0 k = 1;
    #2.9 if (q !== 2'b00) fail("bus IOPATH early");
    #0.2 if (q !== 2'b11) fail("bus IOPATH late");
    #1.1 if (z !== 1'b0) fail("bus-bit INTERCONNECT not applied");
    #0.3 if (z !== 1'b1) fail("z late");
    $display("SELF PASS"); $finish;
  end
endmodule
"""
SELF_SDF = r"""(DELAYFILE
 (SDFVERSION "3.0")
 (DESIGN "top")
 (VOLTAGE 1.200::1.200)
 (PROCESS "1.000::1.000")
 (DIVIDER .)
 (TIMESCALE 1ns)
 (CELL (CELLTYPE "top") (INSTANCE)
  (DELAY (ABSOLUTE
   (INTERCONNECT u\.g\[0\]\.b.X c.A (0.4:0.4:0.4) (0.4:0.4:0.4))
   (INTERCONNECT u\.mem.Q[1] w.A (0.3:0.3:0.3) (0.3:0.3:0.3)))))
 (CELL (CELLTYPE "bufx") (INSTANCE u\.g\[0\]\.b)
  (DELAY (ABSOLUTE (IOPATH A X (2.5:2.5:2.5) (2.5:2.5:2.5)))))
 (CELL
  (CELLTYPE "ramx")
  (INSTANCE u\.mem)
  (DELAY
   (ABSOLUTE
    (IOPATH C Q[0] (1.9:2.0:2.1) (1.9:2.0:2.1))
    (IOPATH C Q[1] (2.9:3.0:3.1) (2.9:3.0:3.1))
   )
  )
  (TIMINGCHECK
    (SETUP (posedge D[0]) (posedge C) (0.1:0.1:0.1))
  )
 )
)
"""


def self_test(steps):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "in").mkdir()
        (d / "in" / "n.v").write_text(SELF_V)
        (d / "in" / "s.sdf").write_text(SELF_SDF)
        main(d / "in" / "n.v", d / "in" / "s.sdf", d / "out", steps)
        src, sdf = d / "out" / "gate_level_netlist.v", d / "out" / "s.sdf"
        b = subprocess.run(["iverilog", "-gspecify", "-ginterconnect", "-g2012", f'-DSDF="{sdf}"', "-s", "tb",
                            "-o", str(d / "t.vvp"), str(src)], capture_output=True, text=True)
        if b.returncode:
            print(b.stdout + b.stderr)
            return False
        r = subprocess.run(["vvp", "-n", str(d / "t.vvp")], capture_output=True, text=True)
        log = r.stdout + r.stderr
        bad = [ln for ln in log.splitlines() if "SDF ERROR" in ln or "SDF WARNING" in ln]
        ok = r.returncode == 0 and "SELF PASS" in log and not bad
        print(f"  steps {','.join(steps) or '(none)'}: {'PASS' if ok else 'FAIL'} "
              + "; ".join(ln.strip()[:90] for ln in log.splitlines() if "SELF" in ln or "SDF" in ln)[:300])
        return ok


if __name__ == "__main__":
    if sys.argv[1:2] == ["--self-test"]:
        full = self_test(STEPS)
        without = {s: self_test(tuple(x for x in STEPS if x != s)) for s in STEPS}
        bare = self_test(())
        need = [s for s, ok in without.items() if ok]
        print(f"self-test: all steps {'PASS' if full else 'FAIL'}; without "
              + ", ".join(f"{s} {'FAIL' if not ok else 'PASS'}" for s, ok in without.items())
              + f"; none {'FAIL' if not bare else 'PASS'}"
              + (" (as required)" if full and not need and not bare else f" (NOT as required: {need or 'none'})"))
        sys.exit(0 if full and not need and not bare else 1)
    if len(sys.argv) != 4:
        sys.exit("usage: sdf_prep.py NETLIST SDF OUT_DIR | --self-test")
    main(*sys.argv[1:])

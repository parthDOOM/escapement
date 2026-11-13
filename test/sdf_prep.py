"""sdf_prep.py NETLIST SDF OUT_DIR: a netlist and SDF pair that Icarus can annotate (test/Makefile, SDF=).

The harden flattens the design, so a cell instantiated inside a module keeps its RTL path as one
escaped name: the clock-gate latches, the buffers on the gated clocks, the SRAM macros, for example
`\\u_core.g_ctx[3].u_ctx.g_r0.g_l[0].l`. The SDF writes that name with escaped dots and brackets
(`u_core\\.g_ctx\\[3\\]...`). Icarus's $sdf_annotate splits an SDF path at every dot, escaped or not,
looks for a submodule `u_core` that the flat netlist does not have, and aborts the simulation
(vpi_scan on a NULL handle). So, in copies of both files, each escaped instance name that holds a
character other than [A-Za-z0-9_$] becomes a plain identifier (each such character becomes `_`,
and a suffix is added if the result is taken). Only names change: every cell, net and connection
is the netlist's own. OUT_DIR gets gate_level_netlist.v and the SDF under the input's file name.

The Makefile also compiles with -ginterconnect: without it Icarus drops every INTERCONNECT (the
wire delays, 75,856 in the 8x4's SDF) with an error line each.

python3 sdf_prep.py --self-test checks both on a two-cell design: an escaped buffer with a 2.5 ns
path delay, then a 0.4 ns wire to a plain one. With the renaming both delays apply, at the right
times; without it the run must fail (it aborts as the chip's run did).
"""
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

INST = re.compile(r"^(\s*)([A-Za-z_]\w*)(\s+)\\(\S+)(\s*\()", re.M)
IDENT = re.compile(r"(?<![\\\w$])[A-Za-z_][\w$]*")


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


def prep_sdf(sdf, ren):
    n = {"instance": 0, "path": 0}

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
        left += [t for t in m.groups() if "\\" in split_path(t)[0] or "\\" in t.rsplit(".", 1)[0]]
    if left:
        raise SystemExit(f"sdf_prep: {len(left)} SDF names not in the netlist's escaped instances, e.g. {left[:3]}")
    return sdf, n


def main(netp, sdfp, outd):
    # cocotb's make reads the Makefile twice: the second read finds a stamp of the same inputs
    stamp = Path(outd) / (Path(sdfp).name + ".sha256")
    key = " ".join(hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (netp, sdfp, __file__))
    if stamp.exists() and stamp.read_text() == key and all(
            (Path(outd) / f).exists() for f in ("gate_level_netlist.v", Path(sdfp).name)):
        print(f"sdf_prep: {outd} is up to date")
        return
    net = Path(netp).read_text()
    ren = rename_map(net)
    sdf, n = prep_sdf(Path(sdfp).read_text(), ren)
    out = Path(outd)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate_level_netlist.v").write_text(prep_netlist(net, ren))
    (out / Path(sdfp).name).write_text(sdf)
    stamp.write_text(key)
    print(f"sdf_prep: {len(ren)} escaped instance names renamed; SDF: {n['instance']} CELL instances, "
          f"{n['path']} INTERCONNECT ends -> {out}")


SELF_V = r"""`timescale 1ns / 1ps
module bufx (input A, output X);
  buf (X, A);
  specify (A => X) = (1.0, 1.0); endspecify
endmodule
module top (input a, output x, output y);
  wire m;
  bufx \u.g[0].b (.A(a), .X(m));
  bufx c (.A(m), .X(x));
  assign y = m;
endmodule
module tb;
  reg a = 0; wire x, y;
  top dut (.a(a), .x(x), .y(y));
  initial begin
    $sdf_annotate(`SDF, dut);
    #10 a = 1;
    // b's path 2.5 ns, the wire to c 0.4 ns, c's own 1.0 ns (the model's, not in the SDF)
    #2.4 if (y !== 1'b0) begin $display("SELF FAIL: IOPATH early"); $finish; end
    #0.2 if (y !== 1'b1) begin $display("SELF FAIL: IOPATH late"); $finish; end
    #1.1 if (x !== 1'b0) begin $display("SELF FAIL: INTERCONNECT not applied"); $finish; end
    #0.3 if (x !== 1'b1) begin $display("SELF FAIL: x late"); $finish; end
    $display("SELF PASS"); $finish;
  end
endmodule
"""
SELF_SDF = r"""(DELAYFILE
 (SDFVERSION "3.0")
 (DESIGN "top")
 (DIVIDER .)
 (TIMESCALE 1ns)
 (CELL (CELLTYPE "top") (INSTANCE)
  (DELAY (ABSOLUTE (INTERCONNECT u\.g\[0\]\.b.X c.A (0.4:0.4:0.4) (0.4:0.4:0.4)))))
 (CELL (CELLTYPE "bufx") (INSTANCE u\.g\[0\]\.b)
  (DELAY (ABSOLUTE (IOPATH A X (2.5:2.5:2.5) (2.5:2.5:2.5)))))
)
"""


def self_test(rename=True):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "in").mkdir()
        (d / "in" / "n.v").write_text(SELF_V)
        (d / "in" / "s.sdf").write_text(SELF_SDF)
        if rename:
            main(d / "in" / "n.v", d / "in" / "s.sdf", d / "out")
            src, sdf = d / "out" / "gate_level_netlist.v", d / "out" / "s.sdf"
        else:
            src, sdf = d / "in" / "n.v", d / "in" / "s.sdf"
        b = subprocess.run(["iverilog", "-gspecify", "-ginterconnect", "-g2012", f'-DSDF="{sdf}"', "-s", "tb",
                            "-o", str(d / "t.vvp"), str(src)], capture_output=True, text=True)
        if b.returncode:
            print(b.stdout + b.stderr)
            return False
        r = subprocess.run(["vvp", "-n", str(d / "t.vvp")], capture_output=True, text=True)
        print("\n".join(r.stdout.splitlines()[-3:]))
        return r.returncode == 0 and "SELF PASS" in r.stdout


if __name__ == "__main__":
    if sys.argv[1:2] == ["--self-test"]:
        ok, bare = self_test(True), self_test(False)
        print(f"self-test: renamed {'PASS' if ok else 'FAIL'}; without renaming "
              f"{'FAIL (as required)' if not bare else 'PASS (the test does not see the bug)'}")
        sys.exit(0 if ok and not bare else 1)
    if len(sys.argv) != 4:
        sys.exit("usage: sdf_prep.py NETLIST SDF OUT_DIR | --self-test")
    main(*sys.argv[1:])

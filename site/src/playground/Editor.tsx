// A CodeMirror 6 editor with Escapement assembly highlighting, an error line and a
// highlighted line linked to the waveform.
import { useEffect, useRef } from "react";
import { EditorState, StateEffect, StateField, RangeSetBuilder } from "@codemirror/state";
import { Decoration, DecorationSet, EditorView, keymap, lineNumbers, highlightActiveLine,
  drawSelection } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { StreamLanguage, syntaxHighlighting, HighlightStyle, bracketMatching } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

const MNEMONICS = new Set(("NOP HALT IRQ RET MOV MOVI MOVHI ADD SUB AND OR XOR CMP TSTI SHL SHR ADDI CMPI LDSH STSH " +
  "JMP JZ JNZ JC JNC DJNZ LDLC CALL RDTIME SETT ADDT WAITT ADDTR EMIT EMITR EMITX EXPECT SAMPLE GETP WAITP GETI " +
  "GETX LDT STT CAPT GETC POP PUSH RXREADY TXREADY").split(" "));

const asm = StreamLanguage.define({
  token(stream) {
    if (stream.eatSpace()) return null;
    if (stream.match(/^[;#].*/)) return "comment";
    if (stream.match(/^\.[A-Za-z_]+/)) return "meta";
    if (stream.match(/^"[^"]*"/)) return "string";
    if (stream.match(/^0x[0-9a-fA-F_]+|^0b[01_]+|^\d+/)) return "number";
    if (stream.match(/^[A-Za-z_][A-Za-z0-9_.]*:/)) return "labelName";
    if (stream.match(/^r[0-3]\b/)) return "variableName.special";
    const m = stream.match(/^[A-Za-z_][A-Za-z0-9_.]*/) as RegExpMatchArray | null;
    if (m) {
      const w = m[0].toUpperCase();
      if (MNEMONICS.has(w)) return "keyword";
      if (["LSB", "MSB", "HOLD", "ARRIVE"].includes(w)) return "atom";
      return "variableName";
    }
    stream.next();
    return null;
  },
});

const style = HighlightStyle.define([
  { tag: t.comment, color: "var(--fg-3)", fontStyle: "italic" },
  { tag: t.keyword, color: "var(--code-k)", fontWeight: "600" },
  { tag: t.meta, color: "var(--code-d)" },
  { tag: t.number, color: "var(--code-n)" },
  { tag: t.labelName, color: "var(--code-l)", fontWeight: "600" },
  { tag: t.special(t.variableName), color: "var(--fg)" },
  { tag: t.atom, color: "var(--code-n)" },
  { tag: t.string, color: "var(--code-d)" },
]);

const setMarks = StateEffect.define<{ error: number | null; focus: number | null; hits: number[] }>();
const errLine = Decoration.line({ class: "cm-err-line" });
const focusLine = Decoration.line({ class: "cm-focus-line" });
const hitLine = Decoration.line({ class: "cm-hit-line" });

const marks = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (!e.is(setMarks)) continue;
      const b = new RangeSetBuilder<Decoration>();
      const doc = tr.state.doc;
      const all: [number, Decoration][] = [];
      const add = (n: number | null, d: Decoration) => {
        if (n != null && n >= 1 && n <= doc.lines) all.push([doc.line(n).from, d]);
      };
      e.value.hits.forEach((n) => add(n, hitLine));
      add(e.value.focus, focusLine);
      add(e.value.error, errLine);
      all.sort((a, z) => a[0] - z[0]).forEach(([p, d]) => b.add(p, p, d));
      deco = b.finish();
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

type Props = {
  value: string;
  onChange: (v: string) => void;
  onRun: () => void;
  onCursorLine: (n: number) => void;
  onFocus: () => void;
  errorLine: number | null;
  focusLine: number | null;
  hitLines: number[];
};

export default function Editor(p: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const cb = useRef(p);
  cb.current = p;
  const external = useRef(false); // a document replaced from outside is not an edit

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: p.value,
        extensions: [
          lineNumbers(), history(), drawSelection(), highlightActiveLine(), bracketMatching(),
          asm, syntaxHighlighting(style), marks,
          keymap.of([
            { key: "Mod-Enter", run: () => { cb.current.onRun(); return true; } },
            { key: "Shift-Enter", run: () => { cb.current.onRun(); return true; } },
            indentWithTab, ...defaultKeymap, ...historyKeymap,
          ]),
          EditorView.updateListener.of((u) => {
            if (u.docChanged && !external.current) cb.current.onChange(u.state.doc.toString());
            if (u.selectionSet && u.view.hasFocus) {
              cb.current.onCursorLine(u.state.doc.lineAt(u.state.selection.main.head).number);
            }
            if (u.focusChanged && u.view.hasFocus) cb.current.onFocus();
          }),
          EditorView.contentAttributes.of({ "aria-label": "Firmware source", spellcheck: "false" }),
        ],
      }),
    });
    view.current = v;
    return () => v.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // replace the document when a new scenario is loaded
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== p.value) {
      external.current = true;
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: p.value } });
      external.current = false;
    }
  }, [p.value]);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    v.dispatch({ effects: setMarks.of({ error: p.errorLine, focus: p.focusLine, hits: p.hitLines }) });
    const n = p.errorLine ?? p.focusLine;
    if (n != null && n <= v.state.doc.lines && !v.hasFocus) {
      v.dispatch({ effects: EditorView.scrollIntoView(v.state.doc.line(n).from, { y: "center" }) });
    }
  }, [p.errorLine, p.focusLine, p.hitLines]);

  return <div className="editor" ref={host} />;
}

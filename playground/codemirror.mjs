// Entry point of the editor bundle (docs/playground/dist/codemirror.js). The page only
// talks to the small API below, so it stays editor-agnostic.
import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { lintGutter, setDiagnostics } from "@codemirror/lint";
import { EditorSelection, StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { basicSetup } from "codemirror";

// Colours come from the MkDocs Material palette, so the editor follows the site's
// light/dark toggle without being rebuilt.
const theme = EditorView.theme({
  "&": {
    color: "var(--md-code-fg-color)",
    backgroundColor: "var(--md-code-bg-color)",
    fontSize: "0.7rem",
    height: "100%",
  },
  ".cm-scroller": { fontFamily: "var(--md-code-font-family)", lineHeight: "1.5" },
  ".cm-content": { caretColor: "var(--md-default-fg-color)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--md-default-fg-color)" },
  ".cm-gutters": {
    backgroundColor: "var(--md-code-bg-color)",
    color: "var(--md-default-fg-color--light)",
    borderRight: "1px solid var(--md-default-fg-color--lightest)",
  },
  ".cm-activeLine": { backgroundColor: "var(--amp-active-line)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--amp-active-line)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--amp-selection) !important",
  },
  ".cm-flash": { animation: "amp-flash 1.2s ease-out" },
  ".cm-tooltip": {
    backgroundColor: "var(--md-default-bg-color)",
    color: "var(--md-default-fg-color)",
    border: "1px solid var(--md-default-fg-color--lightest)",
  },
});

const highlight = HighlightStyle.define([
  { tag: tags.definition(tags.propertyName), color: "var(--md-code-hl-keyword-color)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--md-code-hl-string-color)" },
  { tag: tags.content, color: "var(--md-code-fg-color)" },
  { tag: tags.lineComment, color: "var(--md-code-hl-comment-color)" },
  { tag: [tags.labelName, tags.typeName], color: "var(--md-code-hl-special-color)" },
  { tag: [tags.keyword, tags.meta], color: "var(--md-code-hl-constant-color)" },
  { tag: tags.separator, color: "var(--md-code-hl-punctuation-color)" },
]);

// A short highlight on the line a click jumped to.
const flash = StateEffect.define();
const flashField = StateField.define({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(flash)) {
        deco = e.value === null ? Decoration.none
          : Decoration.set([Decoration.line({ class: "cm-flash" }).range(e.value)]);
      }
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

function lineAt(doc, n) {
  return doc.line(Math.min(Math.max(1, n || 1), doc.lines));
}

export function createEditor(parent, { doc = "", label = "Editor", onChange = () => {} } = {}) {
  const view = new EditorView({
    parent,
    doc,
    extensions: [
      basicSetup,
      yaml(),
      theme,
      syntaxHighlighting(highlight),
      lintGutter(),
      flashField,
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({ "aria-label": label }),
      EditorView.updateListener.of((u) => {
        if (u.docChanged) onChange(u.state.doc.toString());
      }),
    ],
  });

  return {
    view,
    getValue: () => view.state.doc.toString(),
    setValue(text) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
    },
    // findings: [{line, column, severity: "error"|"warning"|"info", message, source}]
    setFindings(findings) {
      const d = view.state.doc;
      const diagnostics = findings.map((f) => {
        const line = lineAt(d, f.line);
        const from = Math.min(line.from + Math.max(0, (f.column || 1) - 1), line.to);
        return {
          from,
          to: line.to > from ? line.to : from,
          severity: f.severity,
          message: f.message,
          source: f.source,
        };
      });
      view.dispatch(setDiagnostics(view.state, diagnostics));
    },
    jumpTo(lineNo, column = 1, { focus = true } = {}) {
      const line = lineAt(view.state.doc, lineNo);
      const pos = Math.min(line.from + Math.max(0, column - 1), line.to);
      view.dispatch({
        selection: EditorSelection.cursor(pos),
        effects: [EditorView.scrollIntoView(pos, { y: "center" }), flash.of(null)],
      });
      // a second transaction restarts the animation when the same line is clicked again
      requestAnimationFrame(() => view.dispatch({ effects: flash.of(line.from) }));
      if (focus) view.focus();
    },
  };
}

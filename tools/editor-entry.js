// CodeMirror 6 pro editor poznámek v hubu (hub/static/vault.js). Sestaví
// tools/build-editor.sh do hub/static/vendor/codemirror.js jako globální CM.
export { EditorState, EditorSelection, StateField, StateEffect, Compartment, RangeSetBuilder,
         Prec, Transaction, Annotation } from '@codemirror/state';
export { EditorView, keymap, Decoration, ViewPlugin, WidgetType, drawSelection, dropCursor,
         placeholder, highlightActiveLine, rectangularSelection } from '@codemirror/view';
export { defaultKeymap, history, historyKeymap, indentWithTab, undo, redo, indentMore,
         indentLess } from '@codemirror/commands';
export { syntaxTree, HighlightStyle, syntaxHighlighting, indentOnInput, ensureSyntaxTree,
         indentUnit } from '@codemirror/language';
export { markdown, markdownLanguage, markdownKeymap, insertNewlineContinueMarkup,
         deleteMarkupBackward } from '@codemirror/lang-markdown';
export { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap,
         startCompletion } from '@codemirror/autocomplete';
export { search, searchKeymap, highlightSelectionMatches, openSearchPanel } from '@codemirror/search';
export { GFM, Strikethrough, Table, TaskList, Autolink } from '@lezer/markdown';
export { tags, Tag, styleTags } from '@lezer/highlight';

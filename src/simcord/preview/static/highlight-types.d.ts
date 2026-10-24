declare module '*highlight/es/core.min.js' {
  interface HighlightRuntime {
    registerLanguage(name: string, grammar: (runtime: HighlightRuntime) => unknown): void;
    highlight(code: string, options: {language: string; ignoreIllegals?: boolean}): {value: string};
  }
  const runtime: HighlightRuntime;
  export default runtime;
}
declare module '*highlight/es/languages/bash.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/cpp.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/csharp.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/css.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/diff.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/dockerfile.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/go.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/ini.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/java.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/javascript.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/json.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/markdown.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/python.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/rust.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/sql.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/typescript.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/xml.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }
declare module '*highlight/es/languages/yaml.min.js' { const grammar: (runtime: unknown) => unknown; export default grammar; }

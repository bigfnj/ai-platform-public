// Minimal types for the vendored web-esheep pet (upstream: bigfnj/web-esheep, GPL-3.0).
// The runtime lives in ./esheep.js; tsc uses this declaration (allowJs is off).
export interface ESheepOptions {
  allowPets?: "none" | "all";
  allowPopup?: "yes" | "no";
  petListUrl?: string;
  petBaseUrl?: string;
}

export default class eSheep {
  constructor(options?: ESheepOptions, isChild?: boolean);
  /** Start the pet. Pass an XML string (parsed inline) or a URL; omit for the embedded default. */
  Start(animation?: string): Promise<eSheep>;
  start(animation?: string): Promise<eSheep>;
  /** Tear down: aborts pending work, clears timers, removes DOM + listeners. */
  remove(): void;
}

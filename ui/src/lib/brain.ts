/**
 * "A MODEL ANSWERED", ASKED WITHOUT NAMING A VENDOR.
 *
 * The server sends the provider's real name in `brain` — `gemini`, `grok`,
 * `openai` — or `local` when the counter's own parser answered. These screens
 * used to compare it against the literal `'grok'`, which was correct for
 * exactly as long as there was one provider: the day the counter was pointed
 * at Google, every model-answered turn started reporting itself as
 * "local · this machine". A label being wrong is bad; a turn claiming the shop
 * answered from its own files when a model routed it is a lie about the one
 * thing this page exists to be clear about.
 */
export const LOCAL_BRAIN = 'local';
export const isModel = (brain: string | null | undefined): boolean =>
  !!brain && brain !== LOCAL_BRAIN;

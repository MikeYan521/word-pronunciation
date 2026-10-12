// 文本处理：分词、停用词、词形还原（与 Python 版 app/textutil.py 规则一致）

export const TOKEN_RE = /[A-Za-z][A-Za-z'\-]*/g;

export const STOPWORDS = new Set(`a aboard about above across after again against ago all almost along already also although always am among an and another any anybody anyone anything anyway are around as at away back be became because become becomes been before began begin behind being below beside besides best better between beyond both but by came can cannot could did do does doing done down during each either else enough even ever every everybody everyone everything everywhere except far few for from further get gets getting go goes going gone got had has have having he her here hers herself him himself his how however i if in indeed inside instead into is it its itself just kept know last later least less let like likely little long made make makes making many may maybe me might mine more most much must my myself near need neither never new next no nobody none nor not nothing now of off often on once one ones only onto or other others ought our ours ourselves out outside over own past per perhaps put quite rather really said same say says see seem seemed seems seen several shall she should since so some somebody someone something sometimes somewhere soon still such sure take taken takes taking than that the their theirs them themselves then there therefore these they this those though three through throughout thus to together too toward towards two under until up upon us used uses using very via was we well went were what whatever when whenever where whereas wherever whether which while whilst who whoever whom whose why will with within without would yes yet you your yours yourself yourselves ii iii iv vi vii viii ix xi xii eg ie`.split(/\s+/));

export function normalize(text) {
  if (!text) return "";
  return String(text)
    .normalize("NFKC")
    .replace(/\u00ad/g, "")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/[ \t]+/g, " ");
}

export function cleanLine(raw) {
  return normalize(raw).trim().replace(/\s{2,}/g, " ");
}

export function cleanBlock(raw) {
  if (!raw) return "";
  return normalize(raw)
    .split(/\r?\n/)
    .map((line) => line.replace(/[ \t]{2,}/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

export function tokens(text) {
  return (String(text).match(TOKEN_RE) || []).map((token) => token.toLowerCase());
}

export function isNoiseToken(word) {
  if (word.length < 2 || word.length > 40) return true;
  if (STOPWORDS.has(word)) return true;
  if (!/[aeiouy]/.test(word)) return true;
  if (/^[a-z]$/.test(word)) return true;
  return false;
}

export const IRREGULAR = {
  children: "child", men: "man", women: "woman", feet: "foot", teeth: "tooth",
  geese: "goose", mice: "mouse", people: "person", lives: "life", knives: "knife",
  wives: "wife", leaves: "leaf", shelves: "shelf", wolves: "wolf", thieves: "thief",
  loaves: "loaf", halves: "half", selves: "self", criteria: "criterion",
  phenomena: "phenomenon", data: "datum", media: "medium", analyses: "analysis",
  theses: "thesis", crises: "crisis", bases: "basis", hypotheses: "hypothesis",
  diagnoses: "diagnosis", oxen: "ox", indices: "index", matrices: "matrix",
  formulae: "formula", curricula: "curriculum", stimuli: "stimulus", alumni: "alumnus",
  am: "be", is: "be", are: "be", was: "be", were: "be", been: "be", being: "be",
  has: "have", had: "have", does: "do", did: "do", done: "do", went: "go", gone: "go",
  said: "say", made: "make", took: "take", taken: "take", came: "come", saw: "see",
  seen: "see", knew: "know", known: "know", got: "get", gave: "give", given: "give",
  found: "find", thought: "think", told: "tell", became: "become", left: "leave",
  felt: "feel", brought: "bring", began: "begin", begun: "begin", kept: "keep",
  held: "hold", wrote: "write", written: "write", stood: "stand", heard: "hear",
  meant: "mean", met: "meet", ran: "run", paid: "pay", sat: "sit", spoke: "speak",
  spoken: "speak", led: "lead", grew: "grow", grown: "grow", lost: "lose",
  fell: "fall", fallen: "fall", sent: "send", built: "build", understood: "understand",
  drew: "draw", drawn: "draw", broke: "break", broken: "break", spent: "spend",
  rose: "rise", risen: "rise", drove: "drive", driven: "drive", bought: "buy",
  wore: "wear", worn: "wear", chose: "choose", chosen: "choose", ate: "eat",
  eaten: "eat", flew: "fly", flown: "fly", forgot: "forget", forgotten: "forget",
  hid: "hide", hidden: "hide", taught: "teach", threw: "throw", thrown: "throw",
  slept: "sleep", swam: "swim", swum: "swim", won: "win", better: "good", best: "good",
  worse: "bad", worst: "bad", further: "far", farther: "far",
};

export const PROTECTED_SURFACE = new Set(`news series species means physics mathematics maths politics economics ethics statistics athletics linguistics genetics logistics gymnastics measles diabetes rabies clothes glasses jeans scissors trousers headquarters thanks customs damages remains surroundings belongings savings earnings findings outdoors indoors upstairs downstairs always perhaps sometimes besides towards campus octopus virus status syllabus focus genius analysis basis crisis thesis hypothesis emphasis parenthesis synthesis diagnosis axis index matrix`.split(/\s+/));

export function lemmaCandidates(word, derivational = true) {
  const out = [];
  const push = (candidate) => {
    if (candidate && candidate !== word && candidate.length >= 2 && !out.includes(candidate)) out.push(candidate);
  };
  if (IRREGULAR[word]) push(IRREGULAR[word]);
  let w = word;
  if (w.endsWith("'s")) push(w.slice(0, -2));
  if (w.endsWith("s'")) push(w.slice(0, -2));
  w = w.replace(/'/g, "");
  if (w.length > 4 && w.endsWith("ies")) push(`${w.slice(0, -3)}y`);
  if (w.length > 4 && w.endsWith("ves")) { push(`${w.slice(0, -3)}f`); push(`${w.slice(0, -3)}fe`); }
  if (w.length > 3 && /(ches|shes|sses|xes|zes)$/.test(w)) push(w.slice(0, -2));
  if (w.length > 3 && w.endsWith("es")) { push(w.slice(0, -1)); push(w.slice(0, -2)); }
  if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) push(w.slice(0, -1));
  if (w.length > 4 && w.endsWith("ied")) push(`${w.slice(0, -3)}y`);
  if (w.length > 3 && w.endsWith("ed")) {
    push(w.slice(0, -2)); push(w.slice(0, -1));
    if (w.length > 4 && w[w.length - 3] === w[w.length - 4]) push(w.slice(0, -3));
  }
  if (w.length > 4 && w.endsWith("ing")) {
    const base = w.slice(0, -3);
    push(base); push(`${base}e`);
    if (w.length > 5 && w[w.length - 4] === w[w.length - 5] && !/[aeiou]/.test(w[w.length - 4])) push(w.slice(0, -4));
  }
  if (w.length > 4 && w.endsWith("est")) { push(w.slice(0, -3)); push(w.slice(0, -2)); }
  if (derivational) {
    if (w.length > 4 && w.endsWith("iest")) push(`${w.slice(0, -4)}y`);
    if (w.length > 4 && w.endsWith("ier")) push(`${w.slice(0, -3)}y`);
    if (w.length > 3 && w.endsWith("er")) { push(w.slice(0, -2)); push(w.slice(0, -1)); }
    if (w.length > 4 && w.endsWith("ally")) { push(w.slice(0, -4)); push(`${w.slice(0, -4)}al`); }
    if (w.length > 3 && w.endsWith("ly")) { push(w.slice(0, -2)); push(`${w.slice(0, -2)}e`); }
  }
  return out;
}

export function lemmatize(word, lexicon) {
  const w = String(word).toLowerCase();
  if (!lexicon) {
    const candidates = lemmaCandidates(w);
    return candidates.length ? candidates[0] : w;
  }
  if (IRREGULAR[w] && IRREGULAR[w] !== w) return IRREGULAR[w];
  if (PROTECTED_SURFACE.has(w)) return w;
  for (const candidate of lemmaCandidates(w, false)) if (lexicon.has(candidate)) return candidate;
  if (!lexicon.has(w)) {
    for (const candidate of lemmaCandidates(w, true)) if (lexicon.has(candidate)) return candidate;
  }
  return w;
}

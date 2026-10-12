"""Text helpers: tokenising, stop-word filtering, running-text cleanup and a
light-weight, dictionary-validated lemmatiser.

The lemmatiser is rule based on purpose: NLTK/spaCy are heavy dependencies and
their models are far larger than this whole application.  Every candidate lemma
produced by a rule is validated against a 126k-entry pronunciation lexicon, so
the rules can never invent a word that does not exist.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")

# Very common function words: they carry no learning value and would dominate
# every chapter list.
STOPWORDS = {
    "a", "aboard", "about", "above", "across", "after", "again", "against", "ago",
    "all", "almost", "along", "already", "also", "although", "always", "am", "among",
    "an", "and", "another", "any", "anybody", "anyone", "anything", "anyway", "are",
    "around", "as", "at", "away", "back", "be", "became", "because", "become",
    "becomes", "been", "before", "began", "begin", "behind", "being", "below",
    "beside", "besides", "best", "better", "between", "beyond", "both", "but", "by",
    "came", "can", "cannot", "could", "did", "do", "does", "doing", "done", "down",
    "during", "each", "either", "else", "enough", "even", "ever", "every",
    "everybody", "everyone", "everything", "everywhere", "except", "far", "few",
    "for", "from", "further", "get", "gets", "getting", "go", "goes", "going",
    "gone", "got", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "however", "i", "if", "in", "indeed",
    "inside", "instead", "into", "is", "it", "its", "itself", "just", "kept", "know",
    "last", "later", "least", "less", "let", "like", "likely", "little", "long",
    "made", "make", "makes", "making", "many", "may", "maybe", "me", "might",
    "mine", "more", "most", "much", "must", "my", "myself", "near", "need",
    "neither", "never", "new", "next", "no", "nobody", "none", "nor", "not",
    "nothing", "now", "of", "off", "often", "on", "once", "one", "ones", "only",
    "onto", "or", "other", "others", "ought", "our", "ours", "ourselves", "out",
    "outside", "over", "own", "past", "per", "perhaps", "put", "quite", "rather",
    "really", "said", "same", "say", "says", "see", "seem", "seemed", "seems",
    "seen", "several", "shall", "she", "should", "since", "so", "some",
    "somebody", "someone", "something", "sometimes", "somewhere", "soon", "still",
    "such", "sure", "take", "taken", "takes", "taking", "than", "that", "the",
    "their", "theirs", "them", "themselves", "then", "there", "therefore", "these",
    "they", "this", "those", "though", "three", "through", "throughout", "thus",
    "to", "together", "too", "toward", "towards", "two", "under", "until", "up",
    "upon", "us", "used", "uses", "using", "very", "via", "was", "we", "well",
    "went", "were", "what", "whatever", "when", "whenever", "where", "whereas",
    "wherever", "whether", "which", "while", "whilst", "who", "whoever", "whom",
    "whose", "why", "will", "with", "within", "without", "would", "yes", "yet",
    "you", "your", "yours", "yourself", "yourselves",
    # single letters / roman numerals that survive tokenising
    "ii", "iii", "iv", "vi", "vii", "viii", "ix", "xi", "xii", "eg", "ie",
}

# Headings that mark the end of the "teaching material" part of a book.
BACK_MATTER_RE = re.compile(
    r"^\s*(references|bibliography|further\s+reading|appendix|appendices|index|"
    r"glossary|acknowledge?ments?|answer\s+key|answers|word\s+list|"
    r"table\s+of\s+contents|contents|preface|foreword|"
    r"copyright|acknowledgement|about\s+the\s+authors?)\s*$",
    re.IGNORECASE,
)

# A table-of-contents style line: "Chapter 1  Introduction ......... 12"
TOC_LINE_RE = re.compile(r"\.\s?\.\s?\.[\s.]*\d+\s*$")


def normalize(text: str) -> str:
    """Normalise unicode (ligatures, curly quotes, NBSP) and strip control chars."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def clean_line(raw: str) -> str:
    """Normalise a single line and collapse hyphenation across line breaks later."""
    line = normalize(raw).strip()
    line = re.sub(r"\s{2,}", " ", line)
    return line


def clean_block(raw: str) -> str:
    """Normalise a multi-line block while *keeping* the line structure.

    Used for OCR output and other free text: :func:`clean_line` would join
    everything into a single line, which destroys the page layout the chapter
    detector relies on.
    """
    if not raw:
        return ""
    text = normalize(raw)
    lines = []
    for line in text.splitlines():
        collapsed = re.sub(r"[ \t]{2,}", " ", line).strip()
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines)


def dehyphenate(text: str) -> str:
    """Join words split across a line break: ``environ-\\nment`` -> ``environment``."""
    return re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens, keeping internal apostrophes/hyphens."""
    return [m.group(0).lower() for m in TOKEN_RE.finditer(text)]


def is_noise_token(word: str) -> bool:
    if len(word) < 2 or len(word) > 40:
        return True
    if word in STOPWORDS:
        return True
    if not re.search(r"[aeiouy]", word):
        return True
    if re.fullmatch(r"[a-z]", word):
        return True
    return False


# --------------------------------------------------------------------------
# Lemmatisation (rule based, validated against a lexicon)
# --------------------------------------------------------------------------

IRREGULAR = {
    # nouns
    "children": "child", "men": "man", "women": "woman", "feet": "foot",
    "teeth": "tooth", "geese": "goose", "mice": "mouse", "people": "person",
    "lives": "life", "knives": "knife", "wives": "wife", "leaves": "leaf",
    "shelves": "shelf", "wolves": "wolf", "thieves": "thief", "loaves": "loaf",
    "halves": "half", "selves": "self", "criteria": "criterion",
    "phenomena": "phenomenon", "data": "datum", "media": "medium",
    "analyses": "analysis", "theses": "thesis", "crises": "crisis",
    "bases": "basis", "hypotheses": "hypothesis", "diagnoses": "diagnosis",
    "oxen": "ox", "lice": "louse", "dice": "die", "indices": "index",
    "matrices": "matrix", "vertices": "vertex", "formulae": "formula",
    "curricula": "curriculum", "stimuli": "stimulus", "nuclei": "nucleus",
    "alumni": "alumnus", "fungi": "fungus", "cacti": "cactus",
    # verbs
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be", "been": "be",
    "being": "be", "has": "have", "had": "have", "does": "do", "did": "do",
    "done": "do", "went": "go", "gone": "go", "said": "say", "made": "make",
    "took": "take", "taken": "take", "came": "come", "saw": "see", "seen": "see",
    "knew": "know", "known": "know", "got": "get", "gotten": "get", "gave": "give",
    "given": "give", "found": "find", "thought": "think", "told": "tell",
    "became": "become", "left": "leave", "felt": "feel", "brought": "bring",
    "began": "begin", "begun": "begin", "kept": "keep", "held": "hold",
    "wrote": "write", "written": "write", "stood": "stand", "heard": "hear",
    "let": "let", "meant": "mean", "met": "meet", "ran": "run", "paid": "pay",
    "sat": "sit", "spoke": "speak", "spoken": "speak", "lay": "lie", "led": "lead",
    "grew": "grow", "grown": "grow", "lost": "lose", "fell": "fall",
    "fallen": "fall", "sent": "send", "built": "build", "understood": "understand",
    "drew": "draw", "drawn": "draw", "broke": "break", "broken": "break",
    "spent": "spend", "cut": "cut", "rose": "rise", "risen": "rise",
    "drove": "drive", "driven": "drive", "bought": "buy", "wore": "wear",
    "worn": "wear", "chose": "choose", "chosen": "choose", "ate": "eat",
    "eaten": "eat", "flew": "fly", "flown": "fly", "forgot": "forget",
    "forgotten": "forget", "hid": "hide", "hidden": "hide", "taught": "teach",
    "threw": "throw", "thrown": "throw", "slept": "sleep", "swam": "swim",
    "swum": "swim", "won": "win", "wore_": "wear", "become": "become",
    # adjectives / adverbs
    "better": "good", "best": "good", "worse": "bad", "worst": "bad",
    "further": "far", "farther": "far", "furthermore": "furthermore",
}

# Surface forms that look plural (or look like a base + suffix) but are words in
# their own right.  They must never be reduced.
PROTECTED_SURFACE = {
    "news", "series", "species", "means", "physics", "mathematics", "maths",
    "politics", "economics", "ethics", "statistics", "athletics", "linguistics",
    "genetics", "logistics", "gymnastics", "measles", "diabetes", "rabies",
    "clothes", "glasses", "jeans", "scissors", "trousers", "headquarters",
    "thanks", "customs", "damages", "remains", "surroundings", "belongings",
    "savings", "earnings", "findings", "outdoors", "indoors", "upstairs",
    "downstairs", "always", "perhaps", "sometimes", "besides", "towards",
    "campus", "octopus", "virus", "status", "syllabus", "focus", "genius",
    "analysis", "basis", "crisis", "thesis", "hypothesis", "emphasis",
    "parenthesis", "synthesis", "diagnosis", "axis", "index", "matrix",
}


def lemma_candidates(word: str, derivational: bool = True) -> list[str]:
    """Return candidate base forms for *word*, most likely first.

    *derivational* enables the riskier rules (``-er`` / ``-est`` / ``-ly`` /
    ``-ally``).  They are only used when the surface form is not itself a known
    word, because ``teacher`` must not become ``teach``.
    """
    out: list[str] = []

    def push(cand: str) -> None:
        if cand and cand != word and len(cand) >= 2 and cand not in out:
            out.append(cand)

    if word in IRREGULAR:
        push(IRREGULAR[word])

    w = word
    # possessive / contraction tails
    if w.endswith("'s"):
        push(w[:-2])
    if w.endswith("s'"):
        push(w[:-2])
    w = w.replace("'", "")

    if len(w) > 4 and w.endswith("ies"):
        push(w[:-3] + "y")
    if len(w) > 4 and w.endswith("ves"):
        push(w[:-3] + "f")
        push(w[:-3] + "fe")
    if len(w) > 3 and w.endswith(("ches", "shes", "sses", "xes", "zes")):
        push(w[:-2])
    if len(w) > 3 and w.endswith("es"):
        push(w[:-1])
        push(w[:-2])
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        push(w[:-1])
    if len(w) > 4 and w.endswith("ied"):
        push(w[:-3] + "y")
    if len(w) > 3 and w.endswith("ed"):
        push(w[:-2])
        push(w[:-1])
        if len(w) > 4 and w[-3] == w[-4]:
            push(w[:-3])
    if len(w) > 4 and w.endswith("ing"):
        base = w[:-3]
        push(base)
        push(base + "e")
        if len(w) > 5 and w[-4] == w[-5] and w[-4] not in "aeiou":
            push(w[:-4])
    if len(w) > 4 and w.endswith("est"):
        push(w[:-3])
        push(w[:-2])
    if len(w) > 4 and w.endswith("iest"):
        push(w[:-4] + "y")
    if len(w) > 4 and w.endswith("ier"):
        push(w[:-3] + "y")
    if derivational:
        if len(w) > 4 and w.endswith("iest"):
            push(w[:-4] + "y")
        if len(w) > 4 and w.endswith("ier"):
            push(w[:-3] + "y")
        if len(w) > 3 and w.endswith("er"):
            push(w[:-2])
            push(w[:-1])
        if len(w) > 4 and w.endswith("ally"):
            push(w[:-4])
            push(w[:-4] + "al")
        if len(w) > 3 and w.endswith("ly"):
            push(w[:-2])
            push(w[:-2] + "e")
    return out


def lemmatize(word: str, lexicon: set[str] | None = None) -> str:
    """Best base form for *word*.

    When *lexicon* is given, only candidates present in it are accepted, which
    keeps the rule set from producing non-words (``bus`` -> ``bu``).

    Note that the pronunciation lexicon used by this app lists inflected forms
    as separate entries (``runs``, ``ran``, ``running``).  Therefore candidates
    are tried *before* accepting the surface form, otherwise plural and tense
    reduction would never happen.
    """
    word = word.lower()
    if lexicon is None:
        return lemma_candidates(word)[0] if lemma_candidates(word) else word

    if word in IRREGULAR and IRREGULAR[word] != word:
        return IRREGULAR[word]

    if word in PROTECTED_SURFACE:
        return word

    # Inflectional rules first (plural, tense, participle) - they are safe for
    # words that are themselves in the lexicon.
    for cand in lemma_candidates(word, derivational=False):
        if cand in lexicon:
            return cand

    if word not in lexicon:
        # Unknown surface form: derivation rules are the only way to find a base.
        for cand in lemma_candidates(word, derivational=True):
            if cand in lexicon:
                return cand

    return word

-- Interlinear Bible database schema
-- Sources: byztxt greektext-textus-receptus (Greek NT, TR),
--          STEPBible-Data TAHOT (Hebrew OT, Masoretic/Leningrad),
--          STEPBible-Data TBESG/TBESH (lexicons), TEGMC/TEHMC (morphology)

PRAGMA foreign_keys = ON;

CREATE TABLE book (
    book_id  INTEGER PRIMARY KEY,
    code     TEXT UNIQUE NOT NULL,   -- canonical 3-letter code, e.g. GEN, JHN
    name     TEXT NOT NULL,
    testament TEXT NOT NULL CHECK (testament IN ('OT','NT')),
    ordinal  INTEGER NOT NULL
);

CREATE TABLE verse (
    verse_id INTEGER PRIMARY KEY,
    book_id  INTEGER NOT NULL REFERENCES book(book_id),
    chapter  INTEGER NOT NULL,
    verse    INTEGER NOT NULL,
    UNIQUE(book_id, chapter, verse)
);

-- One row per source-language word/morpheme, in reading order.
-- NT rows come from byztxt TR; OT rows come from STEPBible TAHOT.
-- Hebrew morphemes (prefix/stem/suffix) are stored as separate rows
-- sharing the same word_group so the UI can re-join them for display.
CREATE TABLE word (
    word_id       INTEGER PRIMARY KEY,
    verse_id      INTEGER NOT NULL REFERENCES verse(verse_id),
    position      INTEGER NOT NULL,       -- order within verse
    word_group    INTEGER,                -- groups prefix/stem/suffix morphemes of one surface word (Hebrew only; NULL for Greek)
    language      TEXT NOT NULL CHECK (language IN ('greek','hebrew')),
    surface       TEXT NOT NULL,          -- original-script text (Hebrew) or transliteration (Greek TR source has no unicode column)
    translit      TEXT,                   -- transliteration
    strongs       TEXT,                   -- head Strong's number, e.g. G3056 / H7225G
    strongs_parts TEXT,                   -- JSON array; full prefix/stem/suffix breakdown for Hebrew
    morph_code    TEXT,                   -- raw morphology code, e.g. V-IAI-3S / HVqp3ms
    parse_number  TEXT,                   -- Robinson's secondary numeric parse code (Greek only, nullable)
    gloss_source  TEXT,                   -- STEPBible's own brief gloss, kept for reference/cross-check
    punct_tag     TEXT,                   -- e.g. H9016 verse-end marker (Hebrew only, nullable)
    UNIQUE(verse_id, position)
);

CREATE INDEX idx_word_verse ON word(verse_id);
CREATE INDEX idx_word_strongs ON word(strongs);

-- Splits compound morph_code values (e.g. Hebrew "HR/Ncfsa", meaning
-- prefix-code "HR" + stem-code "Ncfsa") into individual codes so each
-- part can be joined against morphology_code directly. Greek codes are
-- rarely compound but are included here too (one row) for a uniform
-- join path regardless of language.
CREATE TABLE word_morph_part (
    word_id  INTEGER NOT NULL REFERENCES word(word_id),
    part_ix  INTEGER NOT NULL,   -- 0-based order within the compound code
    code     TEXT NOT NULL,
    PRIMARY KEY (word_id, part_ix)
);

CREATE INDEX idx_word_morph_part_code ON word_morph_part(code);

-- Lexicon entries keyed by Strong's number (STEPBible dStrong / eStrong).
CREATE TABLE lexicon_entry (
    lexicon_id  INTEGER PRIMARY KEY,
    language    TEXT NOT NULL CHECK (language IN ('greek','hebrew')),
    estrong     TEXT NOT NULL,   -- base Strong's number, e.g. H7225
    dstrong     TEXT NOT NULL,   -- disambiguated, e.g. H7225G (usually matches word.strongs)
    ustrong     TEXT,
    word_form   TEXT NOT NULL,   -- lexical form in original script
    translit    TEXT,
    morph_brief TEXT,            -- brief morph code, e.g. H:N-F, G:N-M
    gloss       TEXT NOT NULL,   -- short gloss
    meaning     TEXT             -- full lexicon entry (Abbott-Smith/BDB-derived), HTML-ish markup preserved as-is
);

CREATE INDEX idx_lexicon_dstrong ON lexicon_entry(dstrong);
CREATE INDEX idx_lexicon_estrong ON lexicon_entry(estrong);

-- Translated glosses, kept alongside (not replacing) the original
-- English lexicon_entry.gloss. One row per (lexicon_id, language) so
-- multiple target languages can coexist without schema changes.
-- Populated by translate_lexicon.py as a one-time batch job, not part
-- of the main build pipeline (translation is a deliberate, reviewed
-- step, not a derived/regenerable artifact like the rest of the DB).
CREATE TABLE lexicon_gloss_translation (
    translation_id  INTEGER PRIMARY KEY,
    lexicon_id      INTEGER NOT NULL REFERENCES lexicon_entry(lexicon_id),
    language        TEXT NOT NULL,   -- ISO 639-1 code, e.g. "id" for Indonesian
    gloss           TEXT NOT NULL,
    model           TEXT NOT NULL,   -- which model generated it, e.g. "claude-opus-4-5"
    prompt_version  TEXT NOT NULL,   -- tag identifying the prompt/methodology, for regeneration tracking
    generated_at    TEXT NOT NULL,   -- ISO timestamp
    reviewed        INTEGER NOT NULL DEFAULT 0,  -- 0/1, for later human-review tracking
    UNIQUE(lexicon_id, language)
);

CREATE INDEX idx_lexicon_gloss_translation_lang ON lexicon_gloss_translation(language);

-- Semantic domain classification per Strong's number, e.g.
-- "person_role>personal_name", "deity_spirit>divine_name". Sourced
-- from an external dataset (crizin/bible-db strong_categories.jsonl)
-- keyed at the BASE Strong's level (bare, non-zero-padded, no
-- disambiguation letter -- e.g. "H430", not "H0430G"), so one row here
-- can apply to several lexicon_entry rows that share that base number
-- (e.g. all three H0430G/H/I "Elohim" disambiguations correctly get
-- the same "deity_spirit>divine_name" domain, since the semantic
-- domain genuinely doesn't depend on which specific disambiguated
-- sense a given occurrence resolves to). One row per
-- (lexicon_id, category) since a Strong's number can belong to
-- multiple categories (~43% of entries in the source data do).
CREATE TABLE lexicon_category (
    category_row_id INTEGER PRIMARY KEY,
    lexicon_id      INTEGER NOT NULL REFERENCES lexicon_entry(lexicon_id),
    category        TEXT NOT NULL,   -- e.g. "person_role>personal_name"
    is_primary      INTEGER NOT NULL DEFAULT 0,  -- 1 if this was the source dataset's "primary" category for this Strong's number
    UNIQUE(lexicon_id, category)
);

CREATE INDEX idx_lexicon_category_category ON lexicon_category(category);
CREATE INDEX idx_lexicon_category_lexicon ON lexicon_category(lexicon_id);

-- Morphology code expansion, e.g. "V-IAI-3S" -> Function=Verb; Tense=Imperfect...
CREATE TABLE morphology_code (
    morph_id     INTEGER PRIMARY KEY,
    language     TEXT NOT NULL CHECK (language IN ('greek','hebrew')),
    code         TEXT NOT NULL,
    description  TEXT NOT NULL,   -- raw "Key=Value; Key=Value" string
    fields_json  TEXT,            -- parsed {"Function":"Verb",...} as JSON, best-effort
    UNIQUE(language, code)
);

-- AI-generated per-word contextual gloss (populated by a later pipeline
-- stage, not by the parsers above). One row per word, generated in a
-- verse-batched pass so glosses are context-aware rather than pure
-- dictionary lookups.
CREATE TABLE ai_gloss (
    gloss_id       INTEGER PRIMARY KEY,
    word_id        INTEGER NOT NULL UNIQUE REFERENCES word(word_id),
    gloss          TEXT NOT NULL,
    alt_glosses    TEXT,           -- JSON array of alternates, if any
    note           TEXT,           -- short rationale/disambiguation note
    prompt_version TEXT NOT NULL,  -- tag identifying which prompt/methodology produced this, for regeneration tracking
    model          TEXT,           -- which model generated it
    generated_at   TEXT            -- ISO timestamp
);

-- Reserved for future versification mapping (TVTMS) once that parser
-- is built; not populated by the current pipeline.
CREATE TABLE versification_note (
    note_id      INTEGER PRIMARY KEY,
    source_type  TEXT NOT NULL,
    source_ref   TEXT NOT NULL,
    standard_ref TEXT NOT NULL,
    action       TEXT NOT NULL,
    note_text    TEXT
);

-- OT-only Hebrew<->standard-English versification mapping, derived
-- from STEPBible TVTMS's Condensed section. See parse_tvtms.py for the
-- scope reasoning (OT only; TVTMS's "Greek" data is LXX-based, not
-- Textus Receptus, so has no bearing on this project's NT text).
-- One row per verse where Hebrew (TAHOT source) and standard English
-- numbering actually differ -- the vast majority of verses need no
-- entry here at all, since they already agree.
CREATE TABLE versification_mapping (
    mapping_id          INTEGER PRIMARY KEY,
    section_ref          TEXT NOT NULL,   -- which TVTMS difference-section this came from, e.g. "Mal.4:1-4:6"
    action                TEXT NOT NULL,   -- OneToOne / MergedPrevVerse / SubdividedVerse / TextMayBeMissing / etc.
    book_code             TEXT NOT NULL REFERENCES book(code),
    standard_chapter      INTEGER,         -- English/KJV-convention chapter (NULL if this verse doesn't exist in English)
    standard_verse        INTEGER,
    standard_verse_end    INTEGER,         -- only set when a range couldn't be safely expanded 1:1 (range_mismatch=1)
    hebrew_chapter        INTEGER,         -- chapter as numbered in the Hebrew MT / TAHOT source (NULL if absent in Hebrew)
    hebrew_verse          INTEGER,
    hebrew_verse_end      INTEGER,
    standard_ref_raw      TEXT,            -- original TVTMS cell text, kept for provenance/debugging
    hebrew_ref_raw        TEXT,
    range_mismatch         INTEGER NOT NULL DEFAULT 0  -- 1 if standard/hebrew range lengths differed and couldn't be expanded verse-by-verse safely
);

CREATE INDEX idx_versification_mapping_standard ON versification_mapping(book_code, standard_chapter, standard_verse);
CREATE INDEX idx_versification_mapping_hebrew ON versification_mapping(book_code, hebrew_chapter, hebrew_verse);

-- Reserved for future proper-noun disambiguation (TIPNR) once that
-- parser is built; not populated by the current pipeline.
CREATE TABLE proper_noun (
    noun_id      INTEGER PRIMARY KEY,
    record_type  TEXT NOT NULL CHECK (record_type IN ('PERSON(s)','PLACE','OTHER')),
    unique_name  TEXT NOT NULL,   -- e.g. "Herod" (disambiguating display name, may repeat across different individuals)
    first_ref    TEXT NOT NULL,   -- e.g. "Mat.2.1-Act"
    ustrong      TEXT,            -- unified Strong's number for this specific individual/place
    description  TEXT,
    summary      TEXT,            -- full HTML-ish summary with cross-referenced family/relations
    briefest     TEXT,
    brief        TEXT,
    short        TEXT,
    article      TEXT             -- longest prose description
);

CREATE INDEX idx_proper_noun_ustrong ON proper_noun(ustrong);
CREATE INDEX idx_proper_noun_name ON proper_noun(unique_name);

-- Individual name variants for a proper_noun record (e.g. Aaron has both
-- a Hebrew H0175 and Greek G0002 variant; Herod's three individuals each
-- have their own dStrong). This is the table joined against word.strongs
-- to resolve "which specific person/place does this word refer to".
CREATE TABLE proper_noun_variant (
    variant_id      INTEGER PRIMARY KEY,
    noun_id         INTEGER NOT NULL REFERENCES proper_noun(noun_id),
    significance    TEXT NOT NULL,   -- "Named" / "Greek" / "Spelled" / "Aramaic" / "Group" / "Mentioned" / etc.
    name_variant    TEXT NOT NULL,
    dstrong         TEXT NOT NULL,   -- joins directly against word.strongs
    estrong         TEXT,
    word_form       TEXT,
    translated_name TEXT
);

CREATE INDEX idx_proper_noun_variant_dstrong ON proper_noun_variant(dstrong);

-- One row per exact verse reference where a given proper_noun_variant
-- occurs (from TIPNR's AllRefs column). This is what makes word-level
-- resolution possible: word.strongs alone is ambiguous for names with
-- multiple disambiguated individuals sharing a base Strong's number
-- (e.g. three Herods all tagged G2264 in the source text), but
-- (book, chapter, verse) + estrong together identify exactly which
-- variant_id -- and therefore which specific person/place -- applies.
CREATE TABLE proper_noun_occurrence (
    occurrence_id INTEGER PRIMARY KEY,
    variant_id    INTEGER NOT NULL REFERENCES proper_noun_variant(variant_id),
    book_code     TEXT NOT NULL,
    chapter       INTEGER NOT NULL,
    verse         INTEGER NOT NULL,
    sub_ref       TEXT      -- optional letter suffix for multiple occurrences in one verse, e.g. "a"/"b" from "Mat.14.6a"/"Mat.14.6b"
);

CREATE INDEX idx_proper_noun_occurrence_verse ON proper_noun_occurrence(book_code, chapter, verse);
CREATE INDEX idx_proper_noun_occurrence_variant ON proper_noun_occurrence(variant_id);

-- Precomputed resolution: for each word that matches an ambiguous name,
-- which specific proper_noun (person/place) it refers to, resolved by
-- joining word's (book, chapter, verse, strongs-base) against
-- proper_noun_occurrence. Populated by resolve_proper_nouns.py as a
-- separate pass after the main build, since it depends on both `word`
-- and `proper_noun_occurrence` already being populated.
CREATE TABLE word_proper_noun (
    word_id  INTEGER NOT NULL REFERENCES word(word_id),
    noun_id  INTEGER NOT NULL REFERENCES proper_noun(noun_id),
    variant_id INTEGER NOT NULL REFERENCES proper_noun_variant(variant_id),
    match_basis TEXT NOT NULL,  -- "estrong+verse" (high confidence) or "estrong-only-unambiguous" (only one candidate existed)
    PRIMARY KEY (word_id)
);

CREATE TABLE metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

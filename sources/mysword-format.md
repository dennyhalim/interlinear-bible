# MyReader — MySword Module Format

**Document status:** Specification drafted  
**Reference:** https://www.mysword.info/modules-format  
**Target module type:** Bible (`.bbl.mybible`)

## 1. Overview

MySword content modules use SQLite databases.

Common module extensions:

| Extension | Module type |
|---|---|
| `.bbl.mybible` | Bible |
| `.bok.mybible` | Book |
| `.cmt.mybible` | Commentary |
| `.dct.mybible` | Dictionary |
| `.jor.mybible` | Journal |
| `.mybible` | Personal Notes |

MyReader initially supports Bible modules only.

Other module types are outside the first release scope.

## 2. General implementation rules

1. Preserve the original SQLite database.
2. Preserve original Scripture strings.
3. Interpret formatting during rendering.
4. Support documented optional metadata.
5. Preserve unrecognized tags.
6. Never silently discard unsupported formatting.
7. Keep original tag spelling and capitalization.
8. Separate parsing from visual presentation.

The parser must be tolerant of differences between Bible modules.

## 3. Details metadata

Important documented metadata fields:

| Field | Purpose |
|---|---|
| Abbreviation | Short module identifier |
| VersionDate | Module version date |
| Language | Main language, documented as a 3-letter ISO code |
| RightToLeft | Reading direction |
| OT | Old Testament availability |
| NT | New Testament availability |
| Strong | Strong's reference availability |
| paragraphindent | Default paragraph indentation |
| VerseRules | Regex-based verse formatting rules |
| CustomCSS | Module-specific CSS |

Additional fields may exist.

Implementation requirements:

- Detect fields dynamically.
- Treat optional fields as optional.
- Preserve unknown metadata.
- Do not assume `Language` exists.
- Do not assume `VerseRules` exists.
- Do not assume `paragraphindent` exists.

The TLID module stores `Language: id` in Comments instead of a dedicated Language column.

## 4. General Bible Format (GBF)

MySword uses GBF-style formatting tags.

### 4.1 Paragraph and poetry

| Tag | Meaning |
|---|---|
| `<CM>` | Paragraph end marker |
| `<PI#>` | Poetry indentation |
| `<PF#>` | Poetry first-line indentation |
| `<CI>` | Space separator |

Indentation values for PI and PF range from 0 to 7.

Example:

```text
<PI1><PF1>First poetry line
<PI1><PF0>Following poetry line
```

Rendering requirements:

- Preserve paragraph boundaries.
- Preserve poetry indentation levels.
- Do not invent paragraph boundaries.
- Keep verse numbers inline.
- Use a continuous reading flow when explicit paragraph markers are absent.

### 4.2 Text styling

| Tag | Meaning |
|---|---|
| `<FI>...</Fi>` | Italic or supplied words |
| `<FO>...</Fo>` | Old Testament quotation |
| `<FR>...</Fr>` | Words of Jesus |
| `<FU>...</Fu>` | Underlined text |

The original content must remain unchanged.

Styling is applied only in the presentation layer.

### 4.3 Titles

| Tag | Meaning |
|---|---|
| `<TS>...</Ts>` | Section title |
| `<TS1>`, `<TS2>` etc. | Title variants |

Titles must be displayed separately from ordinary verse text.

### 4.4 Strong's references

| Tag | Meaning |
|---|---|
| `<WH1234>` | Hebrew Strong's |
| `<WG1234>` | Greek Strong's |

The number must be preserved exactly.

Study mode should provide a clickable reference.

A Strong's number does not itself provide a dictionary definition. Definitions require a suitable dictionary data source.

### 4.5 Morphology

Format:

```text
<WTCode>
```

Examples:

```text
<WTNcfsa>
<WTHVqp3ms>
```

The code must be preserved without attempting to reinterpret or rewrite it.

Study mode can display the code and provide a lookup when morphology reference data is available.

### 4.6 Translator notes

Format:

```text
<RF>This is a translator note<Rf>
```

Optional note-link attribute:

```text
<RF q=a>This is a translator note<Rf>
```

Implementation requirements:

- Preserve every note occurrence.
- Do not assume one note per verse.
- Preserve optional note identifiers.
- Display notes separately from Scripture text.
- Do not silently remove note content.

### 4.7 Cross references

Format:

```text
<RX43.3.16>
```

The reference format uses:

```text
Book.Chapter.Verse
```

Verse ranges are also supported:

```text
<RX43.3.16-18>
```

The reader should convert these references into internal book/chapter/verse navigation.

Invalid references must not crash the reader.

## 5. Interlinear format

Interlinear blocks use:

| Tag | Meaning |
|---|---|
| `<Q>...</q>` | Interlinear word/block |
| `<E>...</e>` | English translation |
| `<T>...</t>` | Translation gloss |
| `<X>...</x>` | Transliteration |
| `<D>` | Separator, currently ignored by MySword |
| `<H>...</h>` | Hebrew text |
| `<G>...</g>` | Greek text |
| `<wh>` | Hebrew word marker |
| `<wg>` | Greek word marker |
| `<wt>` | Morphology marker |

Some tags are documented as currently ignored by MySword itself. MyReader should preserve them and determine whether they carry useful information before deciding how to display them.

### Example

```text
<Q>
<H><wh>בְּ</D>רֵאשִׁ֖ית<WH7225><h>
<X>be·re·Shit<x>
<T>In the beginning<t>
<q>
```

An interlinear block should be represented internally as a structured unit, not flattened into an ordinary string.

Suggested internal fields:

```text
InterlinearWord
    original
    strong_number
    morphology
    transliteration
    gloss
    language
```

All fields are optional except the original content.

The parser must support multiple Strong's numbers associated with a single interlinear block.

## 6. HTML support

MySword supports HTML for Bible content, although special Bible tags are preferred.

Potentially useful HTML elements:

- `span`
- `b`, `strong`
- `i`, `em`
- `u`
- `sup`, `sub`
- `br`
- `a`

MyReader must use an allowlist.

Do not execute:

- JavaScript.
- Inline event handlers.
- External scripts.
- Arbitrary embedded objects.
- Untrusted remote resources.

Unknown HTML elements should be preserved in the source but not executed.

## 7. Hyperlink formats

Documented MySword link prefixes:

| Prefix | Purpose |
|---|---|
| `b` | Bible reference |
| `c` | Commentary |
| `n` | Personal notes |
| `d` | Dictionary |
| `s` | Strong's |
| `m` | Morphology |
| `k` | Book |
| `j` | Journal |
| `r` | Popup notes |

Examples:

```html
<a href="b43.3.16">John 3:16</a>
<a href="sH2617">Strong's H2617</a>
<a href="mN-NSF">Morphology</a>
```

Initial implementation priorities:

1. Bible navigation.
2. Strong's references.
3. Morphology references.
4. Popup notes.
5. Commentary and dictionary links when corresponding data is available.

Unsupported targets should produce a safe, non-crashing response.

## 8. VerseRules

MySword supports optional regex-based formatting rules.

The documented format consists of lines containing tab-separated fields:

```text
RegEx<TAB>Replacement<TAB>AlternateReplacement<TAB>ToggleName
```

The final two fields are optional.

Rules may transform text for display.

Security and compatibility requirements:

- Never modify the stored Scripture.
- Preserve the original rule definitions.
- Validate malformed rules.
- Do not execute arbitrary untrusted regex without safeguards.
- Detect potentially expensive patterns.
- Report unsupported rules.
- Test each supported rule against actual module data.

VerseRules support can be added after the basic parser is stable.

## 9. CustomCSS

The `CustomCSS` field allows module-specific styling.

MySword documents theme-aware colors such as:

- `@body`
- `@darken(color, percent)`
- `@lighten(color, percent)`
- `@adjust(color, hue, saturation, lightness)`

Implementation requirements:

- Preserve original CSS.
- Support only a documented safe subset.
- Reject script execution and external resource loading.
- Respect the application's light and dark themes.
- Maintain accessible text contrast.
- Avoid hard-coded bright colors.

Unsupported CSS must not affect Scripture data.

## 10. Parser architecture

Use a two-stage parser.

### Stage A — Tokenization

Identify:

- Plain text.
- Opening tags.
- Closing tags.
- Tag attributes.
- Strong's references.
- Morphology references.
- Interlinear boundaries.
- HTML elements.

### Stage B — Semantic interpretation

Build structured display elements:

```text
Chapter
    Paragraph
        SectionTitle
        Verse
            Text
            StyledText
            InterlinearWord
            StrongReference
            MorphologyReference
            TranslatorNote
            CrossReference
```

This is a conceptual model. The actual source database remains unchanged.

## 11. Error handling

The parser must tolerate:

- Unknown tags.
- Missing closing tags.
- Empty glosses.
- Multiple notes.
- Multiple Strong's references.
- Malformed references.
- Unusual Unicode.
- Hebrew and Greek combining marks.
- Optional metadata.
- Unexpected HTML.

Never crash or discard an entire chapter because one verse has malformed formatting.

## 12. Implementation priority

| Priority | Feature |
|---|---|
| P0 | Plain text and verse boundaries |
| P0 | Paragraphs and section headings |
| P0 | Strong's and morphology preservation |
| P0 | Interlinear blocks |
| P1 | Translator notes |
| P1 | Cross references |
| P1 | Poetry indentation |
| P1 | Basic HTML |
| P2 | VerseRules |
| P2 | CustomCSS |
| P2 | Extended module links |

## 13. Reference

Official specification:

https://www.mysword.info/modules-format

This document describes the intended MyReader compatibility target. Actual implementation support must be verified through automated tests.

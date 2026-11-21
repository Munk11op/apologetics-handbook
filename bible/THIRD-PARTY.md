# Third-party pieces

Everything the app uses that someone else made: what it is, the version, its license, and where it comes from.
"Bundled" means it is inside the page or the repository; nothing bundled is fetched from the internet.

## In the page

| What | Version | License | Where it comes from |
|---|---|---|---|
| Newsreader (reading typeface when Settings → Typeface is "Book"), regular and italic | Google Fonts release, Latin subset (woff2) | SIL Open Font License 1.1 | Bundled: `src/fonts/Newsreader*.woff2` |
| Inter Tight (the main typeface: headings, reading text and controls) | Google Fonts release, Latin subset (woff2), weights 400–700 | SIL Open Font License 1.1 | Bundled: `src/fonts/InterTight.woff2` |
| Bible texts for the verse viewer (only the verses the handbook cites, compressed) | eBible.org "VPL" files, downloaded 2026-11-20 | See the next table | Bundled: `vendor/bible/*_vpl.zip`; build.py writes the cited verses into the page |

No Arabic typeface is bundled: the handbook writes Arabic terms in Latin letters (transliteration) and contains no Arabic script. If Arabic script is ever added, embed a subset of Noto Naskh Arabic (SIL OFL 1.1) the same way.

Hebrew and Greek in the verse viewer use the fonts already on the device (every current phone and computer has fonts for both).

## Bible texts (all from eBible.org; each zip keeps its own "about" page)

| Label | Text | What it is based on | Approach | License |
|---|---|---|---|---|
| BSB | Berean Standard Bible | Greek: modern critical editions with majority readings considered; Hebrew: Masoretic Text | Readable, close to the original wording | Public domain (dedicated by the publisher, 2023) |
| KJV | King James Version (1769 Blayney text, with Apocrypha) | Greek: Textus Receptus; Hebrew: Masoretic (Bomberg) | Formal (word for word), archaic English | Public domain outside the UK (Crown letters patent apply to printing in the UK) |
| ASV | American Standard Version (1901) | Greek: Westcott–Hort-era critical text; Hebrew: Masoretic | Very formal | Public domain |
| YLT | Young's Literal Translation (1898) | Greek: Textus Receptus; Hebrew: Masoretic | Extremely literal, keeps Hebrew tenses | Public domain |
| TR | Greek New Testament, Textus Receptus | The Reformation-era printed Greek text | Original language | Public domain (its age); footnotes dedicated to the public domain by Adam Boyd |
| BYZ | Byzantine Majority Text (Robinson–Pierpont) | The majority of Greek manuscripts | Original language | Public domain |
| TCGNT | Text-Critical Greek New Testament (Robert Adam Boyd) | A critical text built on Robinson's Byzantine Textform 2018 with Boyd's text-critical decisions | Original language | Public domain |
| WLC | Westminster Leningrad Codex | The Leningrad Codex (about AD 1008), the oldest complete Hebrew Bible | Original language | Text public domain. Its lemma and morphology data are CC BY-SA 3.0; the app does not use that data. |

## The Bible reader and word study (the `bible/` folder beside the page)

Each text is one file, `bible/<ID>.js`, loaded only when the reader opens it. Built by `tools/bible/build_full.py`; the text is reformatted only (one verse per entry), never reworded.

| Label | Text | Source | License |
|---|---|---|---|
| BSB, KJV, ASV, YLT, WLC, TR, BYZ, TCGNT | as in the table above | eBible.org VPL files | as above |
| LSV | Literal Standard Version (2020) | eBible.org `englsv_vpl.zip` | Copyright © 2020 Covenant Press, shared under Creative Commons Attribution-ShareAlike 4.0 (lsvbible.com). Shown unchanged with its credit under every chapter. |
| WEB | World English Bible | eBible.org `engwebp_vpl.zip` | Public domain |
| DBY | Darby Translation (1890) | eBible.org `engDBY_vpl.zip` | Public domain |
| GNV | Geneva Bible (1599) | eBible.org `enggnv_vpl.zip` | Public domain |
| DRA | Douay-Rheims (1899, Challoner) | eBible.org `engDRA_vpl.zip` | Public domain |
| LXXE | Brenton's English Septuagint (1851) | eBible.org `eng-Brenton_vpl.zip` | Public domain |
| LXX | Septuagint, Greek (Brenton's text) | eBible.org `grcbrent_vpl.zip` | Public domain |
| VUL | Clementine Vulgate (1592/1598) | eBible.org `latVUC_vpl.zip` | Public domain |
| PESH | Syriac Peshitta (New Testament) | CrossWire SWORD module `Peshitta` | Public domain |
| MUR | Murdock's English Peshitta (1852) | CrossWire SWORD module `Murdock` | Public domain |
| ETH | Etheridge's English Peshitta (1846) | CrossWire SWORD module `Etheridge` | Public domain |

**Word study** (`bible/words/*.js`, built by `tools/bible/build_words.py`): every Hebrew and Greek word with its transliteration, English gloss and Strong's number, and a brief lexicon. Data from STEPBible.org, created by Tyndale House, Cambridge: TAHOT (Hebrew Old Testament, Leningrad Codex), TAGNT (Greek New Testament, with the printed editions each word appears in), TBESH and TBESG (brief Hebrew and Greek lexicons). License: Creative Commons Attribution 4.0. Source: https://stepbible.github.io/STEPBible-Data/ (downloaded 2026-11-20, vendored gzipped in `vendor/stepbible/`). Change note, as the license asks: the data is reformatted only; morphology codes are left out, lexicon definitions longer than about 1,100 characters (Hebrew) or 1,400 characters (Greek) are shortened with "…", and the counts of how each word is rendered are computed from the glosses. The word-study panel names the source and links to it.

Copyrighted translations (LSB, NASB 1995, NKJV, ESV) are **not** bundled. The verse viewer links out to read them (read.lsbible.org, biblegateway.com) and to an interlinear (biblehub.com). The links open only when you tap them.

## Live (speech to text), standalone page

| What | Version | License | Where it comes from |
|---|---|---|---|
| transformers.js (Hugging Face) | 3.7.1, pinned | Apache 2.0 | jsDelivr CDN, downloaded once and cached by the browser |
| Whisper base.en, ONNX (with a graphics card) | onnx-community/whisper-base.en | MIT (OpenAI Whisper weights) | Hugging Face, downloaded once (about 200 MB) and cached |
| Whisper tiny.en, ONNX (without one) | onnx-community/whisper-tiny.en | MIT | Hugging Face, downloaded once and cached |

The hosted page (claude.ai) loads none of these: Live is typing only there.

## Handbook Live (desktop app)

| What | Version | License | Where it comes from |
|---|---|---|---|
| faster-whisper | 1.2.1 | MIT | PyPI, installed by the installer into its own folder |
| Whisper base.en (CTranslate2) | via faster-whisper | MIT | Hugging Face, downloaded once at install |
| soundcard | 0.4.6 | BSD 3-Clause | PyPI |
| pycaw | 20260927 | MIT | PyPI |
| comtypes | (pulled in by pycaw) | MIT | PyPI |
| pywebview | 6.2.1 | BSD 3-Clause | PyPI |
| Python | 3.12.10 (only if no usable Python is found) | PSF License | python.org, downloaded by the installer |

## Development only (never shipped)

| What | License | Used for |
|---|---|---|
| Python-Markdown 3.10, PyYAML | BSD | build.py |
| pytest, Playwright for Python 1.65.2 (Chromium) | MIT, Apache 2.0 | tests and measurements |
| axe-core (`tools/vendor/axe.min.js`, run by measure.py) | MPL 2.0 | accessibility checks |
| Pillow | MIT-CMU | visual regression |
| Node.js 26 | MIT | fast offline scoring of the matcher and its unit tests (see DECISIONS.md) |

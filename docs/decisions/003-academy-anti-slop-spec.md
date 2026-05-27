# 003 — Academy voice + anti-slop spec

**Status:** Proposed
**Date:** 2026-05-23
**Authors:** Bhuvanesh (with Claude)

This is a working spec, not a conventional ADR. The Academy
generation pipeline uses it as the rubric for the editing pass and as
the system prompt for every model call that touches prose. The bar:
a reader who knows financial writing should not be able to detect a
machine in the loop. Calibration writers in the next section.

## The voice we want

Read this list of writers and publications before you write a line for
the Academy. It is the calibration.

* The FT's Pink Pages explanatory boxes. Three columns, one chart, no
  hedging.
* The Economist Schools Briefs.
* Bloomberg Businessweek longform.
* Mint Long Story (Sundeep Khanna era).
* Andy Mukherjee on Indian finance (Reuters Breakingviews, then
  Bloomberg Opinion).
* Tamal Bandyopadhyay's banking columns.
* Niranjan Rajadhyaksha on monetary policy.
* Mythili Bhusnurmath on policy and markets.
* Ira Dugal at Reuters India; her earlier work at BloombergQuint.
* James Crabtree's *The Billionaire Raj*.
* T. N. Ninan's *The Turn of the Tortoise*.
* Howard Marks' memos for the analytical voice: confident,
  undecorated, willing to repeat a word if it is the right word.

The pattern across all of them: declarative sentences, concrete nouns,
specific numbers, a clear writer behind the page who has read the
filing. They do not perform expertise; they show it.

## What we do not sound like

Investopedia. ClearTax explainers. Most LinkedIn finance posts. The
output of a model asked to "write a comprehensive guide". Indian
business school marketing copy. The "Insights" section of a wealth
management firm's website.

If a draft could be plausibly attributed to any of those, it fails.

## Explain, then ground — the prime directive

This is the most important rule in the document. It overrides any
instinct toward dry precision.

The reader does not know the topic yet. That is why they are here. The
job is to *teach*, not to *cite*. A great explainer leads with the
idea in plain language, makes it concrete, and only then brings in the
regulation, the number, or the section reference as the proof that the
explanation is exact.

The failure mode this fights is the legal-memo voice: prose that opens
with a regulation number, summarises sub-clauses, and never tells the
reader what any of it *means* for them. It reads like a lawyer billing
by the citation. It is accurate and useless.

The shape of a good explanatory passage:

1. **The idea, in plain words.** What is this, in a sentence a smart
   sixteen-year-old understands? No jargon, or jargon defined in the
   same breath.
2. **Made concrete.** A worked instance, a named company, a number
   that gives the idea scale, or a short hypothetical. Show it.
3. **The grounding.** Now the precise rule, threshold, or citation
   that makes the explanation authoritative. The citation is the
   evidence *for* the explanation, never a substitute for it.

Rules that follow from this:

* **Lead with the concept, not the statute.** Never open a paragraph
  with "Regulation 6(1) provides…". Open with "There are two ways a
  company can qualify to list. The first is to prove it already makes
  money." *Then* cite Regulation 6(1).
* **Do not over-cite.** Common knowledge carries no citation. "A
  company sells shares to the public to raise money" needs no source.
  Reserve `[^id]` for specific rules, thresholds, dates, and
  non-obvious facts. A citation on every sentence is itself a tell:
  of the lawyer voice, and of a writer hiding behind sources instead
  of explaining. (This refines, it does not loosen, the sourcing
  rules below: every *non-obvious* claim still carries a source.)
* **Define every term at first use, in-line, without breaking flow.**
  "qualified institutional buyers — the big money: mutual funds,
  insurers, pension funds, foreign funds — …". Not a glossary aside;
  a clause.
* **Frame around people and actions, not provisions.** What the
  company does, what the investor experiences, what the regulator is
  trying to prevent. "SEBI keeps fraudsters out of the market" before
  "Regulation 5(1) lists the persons debarred from accessing the
  capital market."
* **Concrete examples are required, not banned.** The ban on
  "Imagine…" / "Picture this…" openers (below) is a ban on the
  *cliché framing phrase*, not on examples. Introduce a hypothetical
  by just stating it: "A profitable manufacturer with three clean
  years of accounts takes the first route." Use real named issues
  and real numbers wherever the corpus supports them.
* **The standfirst paragraph teaches the whole thing in miniature.**
  By the end of the opening paragraph the reader should already be
  able to define the term in their own words. The rest of the article
  earns the detail.

## Banned vocabulary

Three tiers. The editing pass treats them differently.

### Tier 1 — hard ban (delete and rewrite, no exceptions)

These are AI fingerprints. If they appear in a draft, the prose was
either machine-written or machine-edited.

`delve`, `dive into`, `leverage` (as a verb), `robust`, `seamless`,
`comprehensive` (as filler), `nuanced` (as filler), `holistic`,
`paramount`, `pivotal`, `intricate`, `multifaceted`, `myriad`,
`plethora`, `navigate the landscape`, `the landscape of …`,
`ecosystem` (unless literally biology), `journey` (as metaphor),
`unlock` (as metaphor), `harness` (as metaphor), `empower`, `elevate`,
`transformative`, `cutting-edge`, `state-of-the-art`, `game-changing`,
`vibrant`, `bustling`, `thriving`, `world-class`, `next-generation`,
`paradigm`, `synergy`, `in today's …`, `in this day and age`,
`fast-paced`, `ever-evolving`, `rapidly changing`,
`it is important to note that`, `it is worth noting that`,
`it should be noted that`, `in conclusion`, `in summary`,
`to summarize`, `overall`, `all in all`.

### Tier 2 — strong watchlist (allowed only with a specific reason)

These have legitimate uses in financial writing, but models reach for
them as filler. Default to deleting; keep only if the sentence breaks
without them.

* `however` — usually `but` is right.
* `furthermore`, `moreover`, `additionally` — almost always cuttable.
  Real transitions are made through content, not connectives.
* `thus`, `hence`, `therefore` — *so* covers most cases.
* `notably`, `crucially`, `interestingly`, `significantly`
  (as adverb) — throat-clearing. Acceptable only when the next
  clause is genuinely surprising; even then, often deletable.
* `arguably`, `essentially`, `basically`, `fundamentally`,
  `largely`, `broadly speaking`, `to a certain extent`,
  `at the end of the day` — hedges. Cut unless the uncertainty is
  real and named (see *Naming uncertainty* below).
* `significant`, `substantial`, `considerable`, `meaningful`
  (as intensifier), `key` (as filler), `important` (as filler) —
  show why it matters with a number or example, do not assert it.
* `disrupt`, `disruption` — overused to meaninglessness. Use only
  when describing an actual displacement of an incumbent, not as a
  buzzword.

### Tier 3 — simpler alternative usually wins

These are not banned. But where the simpler word is available without
loss of precision, take it.

| Reach for | Try instead |
|---|---|
| utilise / utilize | use |
| facilitate | help / let / make easier |
| endeavour | try |
| commence | start, begin |
| prior to | before |
| subsequent to | after |
| in order to | to |
| a number of | (give the number) |
| a variety of | (be specific) |
| a wide range of | (be specific) |

The Latinate word is sometimes right, especially when paraphrasing
SEBI or RBI prose where the register is formal by convention. The
test is meaning, not register: *commence* and *start* mean the same
thing, so use *start*; *consideration* and *payment* do not mean
exactly the same thing, so keep *consideration* when describing
share-transfer documents.

## Banned constructions

These split into two groups: AI fingerprints (almost certainly machine
output), and bad-writing patterns (humans do them too, but the editing
pass should still strip them).

### AI fingerprints (hard ban)

**The tricolon of vague nouns.** Models love `growth, efficiency, and
innovation`. They love `transparency, accountability, and trust`.
Three-item lists of abstract nouns are a tell. Use two specifics, or
one specific and one example, or no list at all.

**False symmetry.** `On one hand … on the other hand` is a model
fingerprint. Real arguments are not symmetric. Pick a side and name it.
If the question is genuinely contested, say who is on each side.

**The straw counter-argument.** `Some might argue that …`, `Critics
have suggested …`, `Skeptics point out …`. Almost always invented. If
a real critic said something, name them and quote them. Otherwise cut.

**The closing meta-paragraph.** A final paragraph that summarises what
the piece just said. The reader was there. End on the strongest
specific you have. If the natural ending is a number, end on the
number.

**The "Imagine …" opener.** Also `Picture this:`, `It's 2024 and …`,
or any second-person scene-setting. Start with the thing.

**The "Whether you're an X or a Y" opener.** Banned. Also `From X to
Y, …` as an opener. Banned.

**Performed humility.** `It's a complex topic, but let's try to
understand it.` Cut. The reader knows; that is why they clicked.

**Pep-talk closes.** `Armed with this knowledge, you can now …` Cut.

**The faux-rhetorical question.** `So what does this mean for retail
investors?`, immediately followed by the model answering itself. Real
questions in prose are rare and earned. Models reach for them as
transitions.

### Bad-writing patterns (cut on sight)

**The bulletpoint thicket.** Bullets are for genuinely list-shaped
information: categories of investor, items on a timeline, the legs of
a procedure. Prose broken into bullets to look organised is slop. If
three bullets could be two sentences, write the two sentences.

**The em-dash habit.** Em-dashes are fine, sparingly. A draft with one
in every other paragraph is a tell. Replace most with commas, periods,
parentheses, or a sentence break. Target: at most one em-dash per 400
words of body text.

**Phantom precision.** `Approximately 73% of retail investors …`
without a source for the 73 is invented. Either you have the number
and the source, or you say *most* or *more than half*, or you cut the
claim.

**Throat-clearing openings.** A first sentence that announces the
topic instead of starting it. `IPO allotment is a topic that …`. No.
Start mid-action: `When an IPO is oversubscribed, every retail
applicant is treated identically.`

**Filler adverbs.** `Notably`, `interestingly`, `importantly`,
`crucially`, `significantly`, `essentially`. Most are deletable; the
sentence improves.

## Prose architecture

The cadence of a paragraph is as much a tell as the vocabulary. Models
default to a metronome: nine to fourteen words, comma, eleven to
fifteen words, period. Repeat. Humans don't write like that.

* **Vary sentence length on purpose.** After a long, clause-heavy
  sentence, a four-word one. Then back to length. Read a paragraph
  aloud; if it sounds like a treadmill, break the rhythm.
* **Paragraph length follows the thought, not the format.** A one-
  sentence paragraph is a legitimate move when the sentence carries
  weight. A six-sentence paragraph is fine if it is a single arc.
  Three-to-four-sentence paragraphs all the way down is the AI
  default.
* **Transitions move through content, not connectives.** The next
  sentence picks up a specific from the previous one. `SEBI
  introduced the lottery in 2012. The change followed years of
  complaints from retail applicants that the proportional method
  …` is better than `Furthermore, SEBI's 2012 reform …`.
* **Concrete nouns over abstract.** `Zomato's 2021 IPO` beats
  `recent technology listings`. `The ninety days from DRHP filing
  to listing` beats `the typical timeline`. Name names.
* **Open mid-action.** First sentences that name the question or
  drop the reader into the mechanic. Not first sentences that
  announce what the piece is about.
* **Repeat a word if it's the right word.** Models thesaurus-shop to
  avoid repetition and it produces register-mismatched synonyms.
  *Allotment* is a precise term; do not vary it to *distribution* or
  *allocation* for the sake of variety. Vary terminology only when
  the variant is *more* precise, never for style alone.
* **Start sentences with `But`, `And`, `So` sparingly.** A good move,
  but a tic if overused. One or two per piece, not per paragraph.

## Point of view

Default to third person and the named role. `The applicant`, `retail
investors`, `the merchant banker`. Not `you`.

`You` is appropriate in genuinely instructional how-to passages
(`When you bid through UPI, the block on your bank account holds
until allotment`). Use it then; do not use it for exposition. Models
default to `you` because it sounds warm; the cost is that the prose
sounds like a help-centre article instead of reference writing.

Never use `we` to mean the reader and the writer together
(`we can see that …`). This is a model tic. `We` is acceptable when
it refers to the publication's editorial position, and only then.

## Naming uncertainty

Hedges as throat-clearing are banned. Uncertainty that is real is not
a hedge: it is information. Name it.

* `SEBI has not specified how this rule applies to OFS in the
  five years since the amendment.` Acceptable.
* `In practice, the lock-in is often observed but inconsistently
  enforced; the most recent SEBI enforcement order on the question
  is from 2021.` Acceptable.
* `Some uncertainty exists around the timeline.` Not acceptable.
  Either name the source of the uncertainty or cut.

The test: an uncertainty statement must tell the reader something
they did not know before. If it does not, it is a hedge.

## Sources in the manuscript

Mark every cited claim with `[^source-id]` in the manuscript,
referencing an entry in the article's `sources.json`. Whether these
render as footnotes, margin pins, or end-notes is a rendering
decision in the Astro layer, not a writing decision. The manuscript
stays format-agnostic.

## Required moves per paragraph

Every paragraph that makes a claim must do one of these:

1. Name a specific issue, company, or rule (`Zomato`, `Reg 32 of
   ICDR`, `the 2024 amendments to UPI block limits`).
2. Cite a number with a source (`₹3,500 crore`, with source ID).
3. Cite a date with a source.
4. Quote a primary document.

A paragraph that does none of these is either a transition (fine,
keep it short) or filler (cut it).

## Sourcing rules

* SEBI circulars and ICDR Regulations are primary. Cite by section
  and date of amendment.
* NSE and BSE rulebooks are primary for exchange procedure.
* RBI Master Directions are primary for foreign-investor and
  banking-side rules.
* AIBI (Association of Investment Bankers of India) handbook is
  primary for merchant-banker procedure.
* News reports are secondary. Use only for specific event facts
  (`Paytm priced at ₹2,150 per share`) and prefer two independent
  reports for any contested number.
* Investopedia, ClearTax, Groww, Zerodha Varsity, Moneycontrol
  glossary, Chittorgarh are not sources. We may overlap with them;
  we do not cite them.
* Wikipedia is not a source. (A footnote in a Wikipedia article that
  points to a SEBI circular is a usable starting point, but the
  citation goes to the SEBI circular, not to Wikipedia.)
* Every claim that is not common knowledge in Indian capital markets
  must carry a source ID from the harvest corpus.

What counts as "common knowledge" is narrow. The test: if a
first-year MBA at IIM-A would not be expected to know the fact,
source it. *India's two main exchanges are NSE and BSE* is common
knowledge. *The retail lottery method was introduced by SEBI in 2012*
is not; it carries a source.

If the harvest does not support a claim, the claim is cut. Not
softened with `generally` or `typically`. Cut.

## Edit-pass operational instructions

The anti-slop edit stage receives the draft plus this document and
runs as a separate model call. Its prompt is:

> You are editing a draft for the IPO Academy. The voice spec, the
> tiered banned vocabulary, and the construction list are below.
>
> For every sentence, decide: pass, rewrite, or cut.
>
> Tier-1 banned vocabulary and AI-fingerprint constructions must be
> removed in every instance.
>
> Tier-2 vocabulary may stay only when removing it would break the
> sentence; default to cutting.
>
> Tier-3 swaps apply where they do not lose precision.
>
> A rewrite must preserve the factual content, the source tags
> (`[^id]`) attached to each claim, and the article's structure.
> Do not add new claims. Do not add or move source tags. Do not
> change numbers, dates, or named entities.
>
> If a sentence cannot be salvaged without losing meaning, mark it
> `[EDITOR: cut — see note]` with a one-line reason and leave it
> for human review.
>
> Return the edited draft only. No preamble, no summary.

Order of stages:
1. Brief (defines what the article must answer).
2. Source harvest (offline; Claude-driven; produces `sources.json`).
3. Outline (`deepseek-reasoner`, grounded in harvest).
4. Draft (`deepseek-chat`, grounded in harvest; every claim tagged).
5. Fact-check (`deepseek-reasoner`; verifies tags against excerpts).
6. Anti-slop edit (`deepseek-reasoner` with this doc as system).
7. Visual brief (`deepseek-chat`; structured JSON of tables,
   pull-quotes, timelines, sidebars).
8. Human read.

Anti-slop runs *after* fact-check, never before. Anti-slop never
adds claims; it removes or rewrites only.

## Examples

The examples use placeholder source tags `[^s1]` and so on. In a real
draft these resolve to entries in the article's `sources.json`. The
dates and citation specifics in the "After" blocks below are *to be
verified during harvest* — they are written as plausible targets,
not asserted as fact. The discipline is: *no number, date, or rule
citation goes into a draft without a verified source*. Including in
this document.

### Before (slop)

> In today's rapidly evolving financial landscape, understanding how
> IPO allotment works is crucial for retail investors looking to
> navigate the complexities of the primary market. Whether you're a
> first-time investor or a seasoned trader, the allotment process
> involves several key factors that can significantly impact your
> chances of receiving shares. Let's delve into the intricate
> mechanics of this important process.

### After (target shape, source IDs pending)

> Retail allotment in an Indian IPO does not reward size. An
> applicant who bids for one lot and an applicant who bids for
> fourteen lots, in an oversubscribed issue, face the same
> mechanic: a draw. Either the draw assigns a lot, or it does not.
> The principle was set by SEBI [^s1] [date pending verification]
> in response to complaints that the previous proportional method
> handed almost all the shares in popular issues to the largest
> retail applicants.

Notes on the rewrite: no `Whether you're …` opener; no `crucial`,
`navigate`, `delve`, `intricate`; declarative; concrete (the
one-lot vs fourteen-lot comparison); the unverified date is marked
in-line so it cannot escape into a published draft.

### Before (slop)

> The book-building process is a critical mechanism that allows
> companies to discover the optimal price for their shares. It is
> essentially a transparent and efficient method that leverages
> market demand to arrive at a fair valuation.

### After (target shape, source IDs pending)

> Book-building works backwards from demand. The company and its
> bankers publish a price band and take bids over a window set by
> SEBI [^s2] [exact bid-window range to verify]. The final price
> is set where the institutional book is comfortably covered. The
> alternative, the fixed-price issue, survives in India mostly at
> the SME end [^s3].

Notes: no `critical mechanism`, no `essentially`, no `leverages`,
no `optimal`; the active word *works* replaces a string of
abstractions; the unverified specifics are flagged in-line.

### Before (lawyer voice — the failure this spec fights hardest)

This is the real opening of an early draft of *What is an IPO*. It is
not slop in the marketing sense — no banned vocabulary, every claim
sourced. It fails because it explains nothing. It is a statute
summary handed to a reader who came to learn what an IPO is.

> An initial public offer in India is not a marketing event. It is a
> regulated transaction under Chapter II of the SEBI (Issue of Capital
> and Disclosure Requirements) Regulations, 2018. [^s1] The ICDR does
> not define the term "initial public offer" in its definitions
> chapter. The phrase serves as the chapter heading for the set of
> rules that determine who can make such an offer and how. [^s1]
>
> Functionally, an IPO under these regulations is the first public
> issue of specified securities by an issuer whose securities are not
> already listed, satisfying the eligibility conditions of Chapter
> II. [^s1]

Every sentence leads with the regulation. The reader still does not
know what an IPO is, why a company would do one, or what they get out
of it. Three citations in two paragraphs, none of them earning their
place because there is no plain claim for them to support.

### After (explain, then ground)

> An IPO is the first time a company sells its shares to the public.
> Until then, the company is private: owned by its founders, a few
> early investors, maybe a private-equity fund. The IPO opens the
> door. Anyone with a brokerage account can buy in, and the company's
> shares start trading on the NSE and BSE the following week.
>
> Two things can happen in that sale, and usually both do. The
> company can issue new shares and keep the money — capital it uses
> to pay down debt, build a factory, fund the next few years. And the
> early owners can sell some of their existing shares to the new
> public investors, cashing out part of a bet they made years
> earlier. The first is a *fresh issue*; the second is an *offer for
> sale*. Most Indian IPOs are a mix.
>
> None of this is casual. A company cannot simply decide to list. It
> has to clear SEBI's eligibility bar under Chapter II of the ICDR
> Regulations — either by proving a track record of profit, or by
> selling mostly to institutions who are presumed able to judge the
> risk themselves. [^s1] The rest of this piece is about that bar:
> who clears it, who is kept out, and why.

Notes: the reader understands the concept by the end of the first
paragraph. Terms (*fresh issue*, *offer for sale*) are defined in the
breath that introduces them. The single citation lands only when
there is a precise claim — the eligibility bar — for it to support.
The regulation is framed as something the company must *clear*, an
action with stakes, not a heading to be summarised. The detail still
comes; it is now earned.

## Living document

When a new AI tell appears in a draft and gets caught in editing, add
it here. The list grows. Old entries do not come off.

## Related

* `MEMORY.md` — feedback_coherence, feedback_sophistication,
  feedback_radical_restraint
* `docs/decisions/002-parallel-v2-rebuild.md`

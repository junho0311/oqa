# Review scope — a starting point (not a fixed list)

**Every review looks at this one table** — laying out the structure of a system before a review, the scope handed to a review agent or a
blind-check agent, and the "what was looked at and what was not" of the verification stage ([verify.md](verify.md)).

This table is a **starting point**. For each target: (1) sweep it with this table, (2) add the items only that target has (mark them with +),
(3) leave what does not apply as "not applicable (reason)", and (4) check separately "is there more to look at outside the table" before using it.
The targets cannot be limited in advance, because the table is meant for any kind of system: representative items are listed, and whether there are more is reviewed for each target.
The items in the third column are examples — change them into the words that fit the target and add to them.

| Area | Sub-area | Items (examples) |
|---|---|---|
| Purpose and requirements | Problem and scope | what is being solved, who uses it, inside / outside the scope, success criteria |
| | Decisions already made | design decisions, the user's answers, recorded memory — what is not to be argued again |
| Input data | Sources | material received, outside systems, user input, generated data |
| | Quality | completeness, accuracy, freshness, sources disagreeing with each other, **whether the source itself is appropriate** |
| | Format | file format, encoding, loss in extraction, tables, dates, numbers |
| | Sensitive information | personal data, real names, confidential business data, passwords, keys |
| Content and domain | Content | the text and data the system holds or produces, how many per unit, wording (awkward, inconsistent, against the style rules) |
| | Domain rules | regulations, standards, law, industry practice |
| | Flow and procedure | order, exceptions, decision branches, boundaries, missing paths |
| | Judgement and links | the judgements the system makes (classification, scoring, pass or fail) and the logic that links them |
| | Terms | the same thing under different words, industry terms, coined words |
| AI and prompts | Model calls | where they are called, the contract on the answer shape, when the answer comes differently, where results are used as fact, keys, data that leaves |
| Backend | Architecture | module boundaries, dependencies, single place of truth (two pieces doing one job), entry points |
| | Data storage | schema, normalization, integrity (foreign keys, CHECK, triggers), views, history, migrations |
| | Processing logic | loading, calculation, transformation, batch processing, failure paths, error handling, places that fail silently |
| | Interfaces | commands, APIs, file formats, exchange with other systems |
| | Performance and scale | speed, size, growth |
| Frontend | Information architecture | screens, tabs, menus, windows, navigation |
| | UI code | HTML, JS, CSS, state management, the same job in two places |
| | Actual appearance | **captured screens** (main screens, states, long text, empty state), overlap, overflow, hierarchy |
| | Interaction | clicks, input, settings, undo, error guidance |
| | UI text | wording, terms, awkward text, guidance |
| | Design language | agreement with the design document, consistency, accessibility (contrast, keyboard), target environment (desktop only, etc.) |
| Security | Access | authentication, permissions, separation of roles |
| | Secrets and leaks | storage of secrets, logs, exports, ways out to the outside |
| | Input and exposure | input validation, injection, ports, bind address, external scripts |
| | Supply chain | dependency packages, installation, versions |
| Deployment and operation | Build and packaging | what goes out and what is left out, size, forbidden words |
| | Environment and settings | target machines, managed settings, installation constraints |
| | Operation | backup, recovery, audit log, monitoring, procedures |
| Outputs and documents | Outputs | generated files, reports, exports |
| | Documents | README, design, structure, maintenance, how-to, handover — **disagreement with reality** |
| Tests and quality | Tests | what they guard, whether they bite (mutation), missing tests, fixtures |
| | Checks | load checks, UI checks, style checks, false alarms |
| Way of working | Collaboration | version control, review, handover, AI instructions (AGENTS files, skills, hooks) |
| | Compliance | personal data, licenses, security policies |

**(4) Check outside the table** — sweep once more through the concerns that come out of the target's README, design documents, recently recorded
lessons and what the user said this time, and the places where that field commonly breaks; if there is an area or sub-area missing from this table, add it (mark +).

## Depth — how far can this environment look

Being on the list does not mean "looked at". Write for each item the depth that is possible, and in the report the depth actually reached
(do not write "confirmed" for something that was only read — 0 and "did not run" are different).

| Depth | Meaning | In practice |
|---|---|---|
| **Run** | run in a working copy and keep the result | Python scripts, the target's own tests, a local server (127.0.0.1), a browser driver the project already has, read-only queries on a local database |
| **Read only** | read the file, quote and reason. Nothing was run | code, documents, settings, prompts, deployment settings, security (only what reading shows — this is not a security audit) |
| **Not supported** | there is no way — write "not seen (reason)" and do not fill it in by guessing | reproducing on production systems you do not own, browsers that are not installed, penetration testing, fetching outside standards on your own |

Word-processor files, spreadsheets, PDFs and images can be quoted only through a tool that converted them — write the converting tool and what may have been lost together with the quote.

## Candidate questions — per area (these are not tasks; for those not chosen write "not applicable (reason)")

| Area | Candidate questions |
|---|---|
| Purpose and requirements | What is solved, and for whom / is something outside the scope being relied on / how is success recognized, and can each requirement be checked / do requirements or decisions disagree |
| Input data | Per source, how many **entities, not rows** / rows that are filtered, missing or outdated / of two fields with the same meaning, which is the source of truth / "a value exists" differs from "the value is right" — is the distribution plausible / is the source itself right for this use |
| Content and domain | Are the domain rules the user gave kept in this content / do the judgements and recommendations follow logically / are the domain terms used consistently / what would an expert of that field ask first |
| Content and domain — flow and procedure | Do the normal, exception, failure and recovery flows run to the end / are separate links that were actually followed from links guessed from names / who looks at the boundary between two parts / a way around a step everyone assumes is passed through |
| Backend | Is what was returned the same as what was actually stored / when storing fails, does the response say so / paths that skip a check, boundary values, concurrent requests / does the total survive across two steps / do the database constraints really stop what the code relies on |
| Frontend | Can the main flow be finished, and what is shown on failure / is what the screen says the same as what is stored / empty state, long text, pressing twice / can something be reached by its address alone that should not be seen |
| AI and prompts | In how many places is the model called / the expected answer shape, and what if it comes differently / where is the result used as fact, and who confirms it / the ways keys and data leave |
| Security | Who can do what, and is it stopped on the server / where are the secrets, and do they leak through logs or exports / what goes out and where to / is input checked before it reaches a query or a command / which port is opened **on which address** (127.0.0.1 only) |
| Deployment and operation | What goes out and what is left out, and does something go out that must not / settings that differ per environment, things missing in this environment / backup, recovery, monitoring |
| Outputs and documents | Is the conclusion stronger than its basis / was the source text carried over with its provisos / do the numbers match the data and share the same denominator / does the document disagree with the real system / can the reader judge and act |
| Tests and quality | What do the tests guard, and do they bite when the rule is broken / was the expected value built with a yardstick different from the code / is there a happy path and a failure in the middle / is something untested spoken of as tested |
| Way of working | What lies outside the classification so far / was the expected value fixed after reading the code / did the review stop where it got hard / are the instruction and rule documents themselves (examples included) right |

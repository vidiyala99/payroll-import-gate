# payroll-import-gate

> **MOCK/SAMPLE DATA ONLY.** Every input file in `samples/` was written by hand for this demo. The company
> ("Acme Field Services", US + India) is fictional, and so are its workers, IDs and amounts. No real payroll, no
> Rippling data, no Rippling API, no model call, no network access. The "AI" mapping and explanation outputs are
> hand-written fixtures in the shape a model would return. Nothing here describes how Rippling builds its products.

## What it is

This is a small, tested demonstration of how I (Aakash Vidiyala) would approach one part of the charter in Rippling's
public **Financial AI Product** posting
([Senior Software Engineer, Backend - Financial AI Product](https://ats.rippling.com/rippling/jobs/5fbd346a-7e22-4434-96d6-c73429b84fea)):

> "Build workflows that **trace financial values back to source evidence**, surface unresolved gaps, and **prevent
> downstream execution when validation fails**."

> "Create ingestion and normalization systems that can handle messy exports, historical records, customer-provided
> files, and inconsistent provider formats."

The setting is a customer switching payroll providers. They bring three messy files: the prior provider's payroll
register, a time-clock export, and a comp-change sheet. The pipeline:

1. **Maps columns.** Deterministic synonyms come first. A pluggable `MappingAssistant` ("AI-assisted mapping")
   proposes the remaining mappings with a confidence score. The shipped assistant replays a recorded fixture, so the
   tests are deterministic and need no API key. A proposal is accepted only at confidence 0.90 or above, only for a
   target that exists in the schema, and never when it disagrees with a known synonym. A proposal to "ignore" a
   column is refused if that column holds money.
2. **Parses odd formats** (`11/02/2026`, `05-Nov-2026`, `7:00 AM`, `0700`, `8h30m`, `0:30`, `₹18,00,000`,
   `INR 9,60,000`). The parsers never guess: `13/11/2026` under month-first order is an error, not a date.
3. **Stages values with provenance.** Every staged hours, rate, gross and deduction value carries
   file / row / column / original text / transform, down to the cell. Derived values (overtime split per week, rate
   change mid-period, salary proration by days) keep their inputs, so `--trace` prints the whole tree.
4. **Gates the run.** Blocking checks produce an exception queue. Each entry says what is wrong, what a human should
   do, which cells it came from, and which public source the scenario is modeled on.
5. **AI prepares, a human posts.** `PayRun.approve()` needs a named human and is refused while any BLOCK is open.
   `post()` is refused until approval. "Posting" writes to a local SQLite table and nowhere else. Human decisions
   (`accept`, `exclude_worker`) go in a resolutions file and are logged with the reviewer's name.
6. **Explains pay changes and checks the explanation.** It computes the deterministic pay-run diff first, then checks
   each explanation sentence by sentence. Every sentence must name a component that is in the diff. Each number is
   bound to the component named nearest to it and must be that component's prior, current or change value. Direction
   words (rose / fell / stayed) must match the sign of that component's change. "From X to Y" must equal that
   component's prior and current. No component outside the diff (tax, bonus, ...) may be named. Every change must be
   mentioned.

Python 3.11+, standard library only (no third-party dependencies), `unittest`.

## Scenarios and the public sources they are modeled on

"Reported" means one person's public post or a third-party review. It is not a verified defect and not a claim that
Rippling has this bug. Each scenario only motivates a planted test case.

| Scenario | Gate code(s) | Modeled on | Label |
|---|---|---|---|
| Time upload missed a field worker's hours | `HOURS_MISSING`, `DUPLICATE_SHIFT`, `HOURS_LOW` (warn) | r/rippling 2026-07-31: "the upload didn't capture his hours like it should have resulting in him not getting paid" https://www.reddit.com/r/rippling/comments/1vbndl1/employee_not_paid_due_to_server_issue_monday/ | reported |
| Default 9-to-5 pattern filled variable workers' hours | `DEFAULT_SCHEDULE_SUSPECTED` | r/Payroll 2026-10-03: "Rippling just flat out assumed all our employees work 9-5 and assigned them this schedule" https://www.reddit.com/r/Payroll/comments/1wwcir1/anyone_else_gotten_screwed_by_ripplings_new_work/ | reported |
| Totals do not reconcile to the prior register (parallel run) | `REGISTER_RECONCILE_MISMATCH`, `REGISTER_ROW_MISSING` | Rippling blog 2026-10-08: "The most common hurdles are messy employee data and mapping each country's pay codes and deductions correctly. A parallel payroll run before cutover catches errors" https://www.rippling.com/blog/global-payroll-automation | vendor-stated |
| First payroll after switching disagrees with the prior provider | `REGISTER_INTERNAL_MISMATCH` | r/Payroll 2026-10-09: "we cannot come to agreement on payroll discrepancy" https://www.reddit.com/r/Payroll/comments/1x1wotu/anyone_faced_this_with_rippling_implementation/ | reported |
| Prior-provider columns must map to pay codes and deductions | `MAPPING_UNRESOLVED`, `MAPPING_CONFLICT` | Financial AI Product posting: "messy exports, historical records, customer-provided files, and inconsistent provider formats" https://ats.rippling.com/rippling/jobs/5fbd346a-7e22-4434-96d6-c73429b84fea | posting text |
| Comp rows with conflicting effective dates | `COMP_EFFECTIVE_CONFLICT` | `salary_effective_date` in the public v2 compensation schema https://developer.rippling.com/documentation/rest-api/reference/get-compensations.md (no customer report of this exact case was found; it is a planted effective-dating case) | public API schema |
| Pay stated in a foreign currency with no agreed conversion basis | `FX_BASIS_MISSING` | r/rippling 2026-11-19: amended fixed-rate contract converted "on the month of august" https://www.reddit.com/r/rippling/comments/1y09hyo/how_does_conversion_rate_in_the_new_ammended/ | reported |
| Worker or currency booked to the wrong legal entity | `ENTITY_MISMATCH`, `CURRENCY_MISMATCH` | EmployerRecords 2026-11-08: "multi-entity setups draw the sharpest complaints" https://employerrecords.com/eor/rippling | reported |
| A country rule changes inside the pay period, or none is in force | `RULE_CHANGE_IN_PERIOD`, `RULE_MISSING` | r/rippling 2026-10-21: a user asks whether tax-rate changes have a change log https://www.reddit.com/r/rippling/comments/1xbu9ff/change_logs/ | reported |
| Values that cannot be traced or parsed stay open | `VALUE_UNPARSEABLE`, `WORKER_UNMATCHED`, `TIMECLOCK_HOURS_INCONSISTENT`, `RATE_MISSING`, `NEGATIVE_VALUE` | Posting: "surface unresolved gaps, and prevent downstream execution when validation fails" https://ats.rippling.com/rippling/jobs/5fbd346a-7e22-4434-96d6-c73429b84fea | posting text |
| Explaining why pay changed | explanation checker | Rippling AI launch post: "My paycheck was lower this pay period than last pay period ... what happened?" https://www.rippling.com/blog/introducing-rippling-ai | vendor-stated |
| AI prepares, a human posts | `PayRun.approve` / `post` | Rippling VP Controller, 2026-09-22: "Auditors are comfortable with AI preparing the work. They're not yet comfortable with AI posting it." https://www.rippling.com/blog/payroll-reconciliation | vendor-stated |

The table is generated by hand from `gate/catalog.py`. A test checks that every scenario URL in the catalog appears
here and that every Reddit or review-site source is labeled "reported".

## The demo dataset

`samples/acme-switch/` is the set "as delivered", with these problems planted:

- W003, a variable-schedule field tech: every entry is exactly 09:00-17:00 with source `schedule`
- W005, a new field tech: no time entries at all
- an unknown badge `9999` in the time clock
- a `Misc Ded` register column that the assistant maps at confidence 0.55
- W004: two comp rows on the same date with different rates
- W102 (India): an amended contract stated in USD with no conversion basis
- W101 (India): booked to the US company code in the parallel-run period

`samples/acme-switch-clean/` is the same company after the fixes. It passes the gate with an empty queue. Its staged
gross equals the prior register for every worker, including an overtime week, a rate change on 2026-11-09, an
overnight shift and Indian digit grouping.

## Eval results (run 2026-11-21, after review round 1)

`samples/evals/cases.json` holds 33 defect cases and 11 clean cases. Each one is a labeled, hand-written patch to the
clean dataset, so every planted defect can be read in one file.

- A **false accept** is a defect case where the gate did not raise a BLOCK with the expected code, meaning the bad
  value would have reached a pay run. The run passes only if this count is 0.
- A **false reject** is a clean case that the gate blocked.

| Metric | Result |
|---|---|
| Defect cases caught | 33 / 33 |
| **False-accept rate** | **0.0% (0 / 33)** |
| False-reject rate | 9.1% (1 / 11) |
| Explanation checks correct | 7 / 7 (2 faithful model fixtures and 2 template explanations accepted; 3 unfaithful fixtures caught) |

Review round 1 added 12 defect cases: dropping each required column (Co Code, Period Beg, Rate Type, Effective,
Work Date), an hourly worker with no shifts and no expected minimum, a rule version that starts after the period
start, an entity with no configuration, negative gross / hours / rate, and a hours-only duplicate entry. Before the
fix, the dropped-column and missing-rule cases crashed `build()` instead of blocking.

The one false reject is on purpose: `C08-field-worker-really-9to5` is a variable-schedule worker who really did work
09:00-17:00 every day, and the prior register agrees. The gate cannot tell that apart from a template fill, so it
asks a human. That is the trade-off I chose: a few false rejects buy zero false accepts.

The three unfaithful fixtures, and what the checker reports:
- `E3` says pay fell "by $30.00 because your tax withholding increased". Flagged: 30.00 is not a gross value (the
  change was 28.00), tax is named although it is not in the diff, "increased" contradicts the fall, and the overtime
  change is left out.
- `E6` (a deliberately wrong explanation) says "Your gross pay rose by $28.00" when gross fell by 28.00. Every number is in the
  diff, so a checker that only matches numbers would accept it. Flagged as wrong direction.
- `E7` (a deliberately wrong explanation) says "overtime hours went from 0 to 28". 28 is in the diff (as the gross change and
  the rate), so a checker that only matches numbers would accept it. Flagged because 28 is not an overtime-hours value.

Limits of the checker: it is lexical. It binds a number or a direction word to the nearest named component in the
same sentence, so unusual phrasing (pronouns, a number far from its noun, negations like "did not rise") can be
misbound. A false accept is still possible for wording outside these patterns. It is a guard, not proof.

Full output: `companies/rippling/evidence/plan-b-run-2026-11-21.txt`.

## How to run

```
cd companies/rippling/build/payroll-import-gate
python -m gate run samples/acme-switch --trace W002:gross     # exception queue + provenance tree (exit 3: blocked)
python -m gate run samples/acme-switch-clean                  # empty queue, READY_FOR_APPROVAL
python -m gate post samples/acme-switch --approver "A. Reviewer"        # REFUSED (blocked)
python -m gate post samples/acme-switch-clean --approver "A. Reviewer"  # POSTED to in-memory SQLite
python -m gate explain samples/acme-switch-clean W002         # pay-run diff + checked explanation
python -m gate eval samples/evals                             # eval report; exit 0 only if it passes
python -m unittest discover -s tests -t .                     # unit tests
```

## What is NOT claimed

- **Nothing about Rippling's internals.** I have no access to Rippling's code, data model, payroll engine,
  implementation tooling or AI stack. The checks and the design here are my own, not a description of Rippling's.
- **No real data.** Every file is mock data written by hand. Results on 44 hand-labeled cases say nothing about
  accuracy on real customer files.
- **No model was called.** The "AI-assisted mapping" and the model explanations are recorded, hand-written fixtures.
  The `MappingAssistant` protocol is where a real model would plug in, and its output would face the same acceptance
  rules. Measuring a real model is future work.
- **No payroll or tax correctness.** Deductions are carried over from the prior register, not computed. The deduction
  amounts in the samples are illustrative placeholders: the India TDS figures, for example, follow neither the old nor
  the new regime (under the new regime with the section 87A rebate, TDS on the ₹9.6L salary could be zero). The country
  "rules" in `entities.json` are sample configuration, not statutory claims for the US or India.
- **Simplified overtime.** Overtime is split per ISO week (Monday to Sunday) inside the pay period. An overnight shift
  counts entirely on its start date. A week that straddles the period boundary is only counted for the days inside
  the period, so hours from the previous period's part of that week do not count toward the threshold. Overtime hours
  are the last hours worked in the week, paid at the rate in force on that shift's date. Real rules (daily overtime,
  state rules, workweek definitions) are not modeled.
- **Posting guard.** If a human accepts `HOURS_MISSING` or `RATE_MISSING`, that worker has no staged gross and
  `post()` skips them (logged as `skipped_no_gross`). Nothing is posted for a worker without a gross.
- **The Reddit and review posts are single, unverified accounts** (labeled "reported"). They motivated the test
  cases. They are not evidence of a Rippling defect.
- **Not published.** This code is local to this repository. It has not been pushed to any public host.

## Layout

```
gate/catalog.py    exception codes, actions, and the public source for each scenario
gate/parsers.py    deterministic parsers (dates, times, hours, amounts with Indian grouping)
gate/mapping.py    synonym mapping + MappingAssistant protocol + RecordedMappingAssistant (fixture)
gate/pipeline.py   parse, stage with provenance, blocking checks, human resolutions
gate/run.py        PayRun approve/post, local SQLite audit log and posted lines
gate/explain.py    pay-run diff, template explanation, faithfulness checker
gate/evals.py      labeled eval runner and report
samples/           mock datasets (as delivered, clean) and the eval cases
tests/             unittest suite
```

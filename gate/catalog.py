"""Exception codes and the PUBLIC sources each scenario is modeled on.

MOCK/SAMPLE DATA DEMONSTRATION. The scenarios below are modeled on public statements
(Rippling's own blog posts and job posting, a third-party review site, and single,
unverified Reddit posts labeled "reported"). They are not claims about Rippling's
internals, and a "reported" post is one person's account, not a proven defect.
"""

from __future__ import annotations

from dataclasses import dataclass

POSTING_URL = "https://ats.rippling.com/rippling/jobs/5fbd346a-7e22-4434-96d6-c73429b84fea"


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    modeled_on: str  # verbatim quote from the public source
    url: str
    label: str  # "reported" | "vendor-stated" | "posting text" | "public API schema"
    date: str


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in [
        Scenario(
            "S1-hours-missing",
            "Time upload missed a field worker's hours",
            "We upload time from our field service software and the upload didn't capture his hours "
            "like it should have resulting in him not getting paid",
            "https://www.reddit.com/r/rippling/comments/1vbndl1/employee_not_paid_due_to_server_issue_monday/",
            "reported",
            "2026-07-31",
        ),
        Scenario(
            "S2-default-schedule",
            "A default 9-to-5 pattern filled variable workers' hours",
            "Rippling just flat out assumed all our employees work 9-5 and assigned them this schedule "
            "... this will control payroll and benefits",
            "https://www.reddit.com/r/Payroll/comments/1wwcir1/anyone_else_gotten_screwed_by_ripplings_new_work/",
            "reported",
            "2026-10-03",
        ),
        Scenario(
            "S3-parallel-run",
            "Totals do not reconcile to the prior provider's register",
            "The most common hurdles are messy employee data and mapping each country's pay codes and "
            "deductions correctly. A parallel payroll run before cutover catches errors",
            "https://www.rippling.com/blog/global-payroll-automation",
            "vendor-stated",
            "2026-10-08",
        ),
        Scenario(
            "S3b-first-payroll-discrepancy",
            "First payroll after switching disagrees with the prior provider",
            "our first payroll with Rippling, because we cannot come to agreement on payroll discrepancy",
            "https://www.reddit.com/r/Payroll/comments/1x1wotu/anyone_faced_this_with_rippling_implementation/",
            "reported",
            "2026-10-09",
        ),
        Scenario(
            "S4-mapping",
            "Prior-provider columns must map to pay codes and deductions",
            "Create ingestion and normalization systems that can handle messy exports, historical records, "
            "customer-provided files, and inconsistent provider formats.",
            POSTING_URL,
            "posting text",
            "2026-09-24",
        ),
        Scenario(
            "S5-comp-effective-date",
            "Compensation rows with conflicting effective dates",
            "salary_effective_date (public v2 compensation schema). No customer report of this exact case "
            "was found; it is a planted case for effective-dated data.",
            "https://developer.rippling.com/documentation/rest-api/reference/get-compensations.md",
            "public API schema",
            "2026-11-21",
        ),
        Scenario(
            "S6-fx-basis",
            "Pay set in a foreign currency with no agreed conversion basis",
            "the amount in the new ammended contract is not the same and they use the conversion on the "
            "month of august which i sign my first contract",
            "https://www.reddit.com/r/rippling/comments/1y09hyo/how_does_conversion_rate_in_the_new_ammended/",
            "reported",
            "2026-11-19",
        ),
        Scenario(
            "S7-multi-entity",
            "Worker or currency booked to the wrong legal entity",
            "multi-entity setups draw the sharpest complaints",
            "https://employerrecords.com/eor/rippling",
            "reported",
            "2026-11-08",
        ),
        Scenario(
            "S8-rule-change",
            "A country rule changes inside the pay period",
            "asks whether tax-rate changes have a change log (user question, unverified)",
            "https://www.reddit.com/r/rippling/comments/1xbu9ff/change_logs/",
            "reported",
            "2026-10-21",
        ),
        Scenario(
            "S9-unresolved-gap",
            "Values that cannot be traced or parsed stay open",
            "Build workflows that trace financial values back to source evidence, surface unresolved gaps, "
            "and prevent downstream execution when validation fails.",
            POSTING_URL,
            "posting text",
            "2026-09-24",
        ),
        Scenario(
            "S10-explain",
            "Explaining why pay changed",
            "My paycheck was lower this pay period than last pay period ... what happened?",
            "https://www.rippling.com/blog/introducing-rippling-ai",
            "vendor-stated",
            "2026-03",
        ),
        Scenario(
            "S11-prepare-not-post",
            "AI prepares, a human posts",
            "Auditors are comfortable with AI preparing the work. They're not yet comfortable with AI "
            "posting it.",
            "https://www.rippling.com/blog/payroll-reconciliation",
            "vendor-stated",
            "2026-09-22",
        ),
    ]
}


@dataclass(frozen=True)
class Code:
    code: str
    severity: str  # BLOCK stops the run; WARN is shown but does not block
    scenario: str
    action: str  # what a human should do


CODES: dict[str, Code] = {
    c.code: c
    for c in [
        Code("MAPPING_UNRESOLVED", "BLOCK", "S4-mapping",
             "Confirm which target field this column maps to (or that it can be dropped), then rerun."),
        Code("MAPPING_CONFLICT", "BLOCK", "S4-mapping",
             "Two sources disagree on this column's meaning. Pick one mapping, then rerun."),
        Code("VALUE_UNPARSEABLE", "BLOCK", "S9-unresolved-gap",
             "Fix the source cell (the trace gives file, row and column) or confirm the intended value."),
        Code("WORKER_UNMATCHED", "BLOCK", "S9-unresolved-gap",
             "Match this record to a worker in the roster, or confirm it does not belong to this company."),
        Code("HOURS_MISSING", "BLOCK", "S1-hours-missing",
             "Get this worker's hours from the source time system before paying, or exclude them from the run."),
        Code("DEFAULT_SCHEDULE_SUSPECTED", "BLOCK", "S2-default-schedule",
             "Check these hours against the field system or the worker. Exact 09:00-17:00 for a variable "
             "worker usually means a template filled the field."),
        Code("TIMECLOCK_HOURS_INCONSISTENT", "BLOCK", "S9-unresolved-gap",
             "The export's hours column disagrees with its own in/out times. Confirm which is right."),
        Code("DUPLICATE_SHIFT", "BLOCK", "S1-hours-missing",
             "Two shifts overlap for one worker. Remove the duplicate in the source export."),
        Code("RATE_MISSING", "BLOCK", "S9-unresolved-gap",
             "No pay rate is effective for this worker in the period. Add the comp row."),
        Code("COMP_EFFECTIVE_CONFLICT", "BLOCK", "S5-comp-effective-date",
             "Two different rates share an effective date. Keep the correct row."),
        Code("FX_BASIS_MISSING", "BLOCK", "S6-fx-basis",
             "Pay is stated in a currency other than the entity's. A human must set the conversion basis "
             "(rate and date) in the contract; the gate will not pick one."),
        Code("ENTITY_MISMATCH", "BLOCK", "S7-multi-entity",
             "The prior register books this worker to a different legal entity. Confirm the entity."),
        Code("CURRENCY_MISMATCH", "BLOCK", "S7-multi-entity",
             "The prior register's currency does not match the worker's entity. Confirm the currency."),
        Code("RULE_MISSING", "BLOCK", "S8-rule-change",
             "No country rule (or no pay period) is configured for this entity at the period start. Add it."),
        Code("NEGATIVE_VALUE", "BLOCK", "S9-unresolved-gap",
             "A negative hours, rate or amount value needs a human to confirm it (reversal, correction, or typo)."),
        Code("RULE_CHANGE_IN_PERIOD", "BLOCK", "S8-rule-change",
             "A country rule version starts inside this pay period. Decide how the period is split."),
        Code("REGISTER_ROW_MISSING", "BLOCK", "S3-parallel-run",
             "The prior register has no row for this worker in the parallel-run period. Confirm why."),
        Code("REGISTER_RECONCILE_MISMATCH", "BLOCK", "S3-parallel-run",
             "Staged gross does not match the prior provider for the same period. Use the breakdown to "
             "find which side is wrong before cutover."),
        Code("REGISTER_INTERNAL_MISMATCH", "BLOCK", "S3b-first-payroll-discrepancy",
             "The prior register's own gross minus deductions does not equal its net. Ask the prior provider."),
        Code("HOURS_LOW", "WARN", "S1-hours-missing",
             "Hours are below this worker's usual minimum. Check that the upload is complete."),
        Code("SHIFT_OUTSIDE_PERIOD", "WARN", "S9-unresolved-gap",
             "This shift is outside the pay period and was not counted."),
        Code("SHIFT_LONG", "WARN", "S9-unresolved-gap",
             "Shift longer than 14 hours. Check for a missed clock-out."),
    ]
}

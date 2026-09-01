"""Per-call claim state: turn one record_field tool call into a Verdict.

validators.py is stateless — one pure function per field. Two of those functions
need the caller's policy record, which only exists once the policy number has
been accepted. This module holds that state, plus the accepted values so far.
"""

from validators import (ACCEPTED, REJECTED, Verdict, load_policies,
                        validate_callback_phone, validate_claimant_name,
                        validate_date_of_loss, validate_description,
                        validate_loss_type, validate_policy_number)

NEEDS_POLICY = ("claimant_name", "date_of_loss")


class ClaimRecord:
    """Accepted field values for one call, plus the policy they belong to."""

    def __init__(self, policies=None, today=None):
        self.policies = load_policies() if policies is None else policies
        self.today = today   # pinned in tests; None means date.today()
        self.policy = None   # resolved when policy_number is accepted
        self.fields = {}     # accepted values only — never a rejected one

    def record(self, field, value):
        verdict = self._validate(field, value)
        if verdict.status == ACCEPTED:
            self.fields[field] = verdict.value
            if field == "policy_number":
                self.policy = next(p for p in self.policies
                                   if p["policy_number"] == verdict.value)
        return verdict

    def _validate(self, field, value):
        if field == "policy_number":
            return validate_policy_number(value, self.policies)
        if field == "callback_phone":
            return validate_callback_phone(value)
        if field == "loss_type":
            return validate_loss_type(value)
        if field == "description":
            return validate_description(value)
        if field in NEEDS_POLICY:
            if self.policy is None:
                # Name what failed and what to ask for next: the agent reads
                # this back rather than guessing its way around the gap.
                return Verdict(REJECTED, None,
                               f"cannot check {field} before a policy number is on file",
                               "Before I take that, could I have your policy number?")
            if field == "claimant_name":
                return validate_claimant_name(value, self.policy)
            return validate_date_of_loss(value, self.policy, today=self.today)
        return Verdict(REJECTED, None, f"unknown field {field!r}",
                       "Sorry, I didn't catch which detail that was.")

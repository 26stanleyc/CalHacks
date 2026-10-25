"""The trust line: how much of the limit a human asked for their agent actually gets.

A mandate says what the human authorized: "up to $250 a payment, $1,000 a day, groceries and
household". Nothing today checks whether the person behind that mandate is who they say they are
before the agent starts spending. An invented identity can write itself any mandate it likes, and
the first anyone learns of it is when the payments start.

So the mandate is the *request*, and CRS underwrites it. The bureau's answer about the human sets
a band, and the band sets how much of the requested limit the agent is given:

| band       | when                                                      | of the request |
|------------|-----------------------------------------------------------|----------------|
| full       | identity verified, a scoreable file, low fraud risk        | 100%           |
| standard   | identity verified, a file, nothing alarming                | 60%            |
| starter    | thin or no file, or CRS couldn't settle it                 | 25%            |
| restricted | identity failed, flagged deceased, or high fraud risk      | 2%, and a floor|

One rule holds above all of it: **the trust line never raises a cap**. The human's mandate is the
ceiling and CRS can only come down from it. A good credit file is not a reason to let an agent
spend more than its owner allowed, and an agent whose owner asked for $20 keeps $20 whatever the
bureau says. That keeps this underwriting and not a sales pitch.

This is the one place a credit score is read, and it is read about the *person at enrollment*,
never about a payment: a clean credit file says nothing about whether an agent was hijacked an hour
ago. Scoring payments stays with the specialists.

In production this is a soft pull the consumer asks for when they set their agent up, which is a
different permissible purpose from pulling a file to judge a transaction. Here every person is one
of CRS's fake sandbox test identities.
"""

from dataclasses import dataclass

from contracts.schemas import Mandate
from engine.crs_enrollment import FAILED, CrsProfile

# band -> (share of the requested cap, what it means)
BANDS: dict[str, tuple[float, str]] = {
    "full": (1.00, "identity verified at the bureau, a scoreable credit file, low fraud risk"),
    "standard": (0.60, "identity verified, a credit file on record"),
    "starter": (0.25, "little or nothing on file: the agent starts small and earns the rest"),
    "restricted": (0.02, "the bureau could not stand behind this identity"),
}
FLOOR_CENTS = 500  # a restricted agent can still buy something small: $5
GOOD_SCORE = 680  # a scoreable file at or above this reads as established credit
# Below this, capacity is thin. It lowers the line and never refuses the agent: a poor credit file
# is a reason to lend less, not a reason to think a payment is fraud.
THIN_SCORE = 620
HIGH_RISK = 75  # FraudFinder's high band, as risk_level uses it


@dataclass(frozen=True)
class TrustLine:
    """What the agent is actually given, and why."""

    band: str
    per_payment_cap_cents: int
    daily_cap_cents: int
    share: float
    reasons: tuple[str, ...]

    @property
    def explanation(self) -> str:
        return f"{self.band} trust line ({int(self.share * 100)}% of the requested limit): " + "; ".join(self.reasons)


def band_for(profile: CrsProfile) -> tuple[str, tuple[str, ...]]:
    """Which band the bureau's answer puts this person in, with the reasons in plain words."""
    if not profile.available:
        return "starter", ("CRS could not be reached, so the agent starts on a starter line",)
    if profile.verified == FAILED:
        return "restricted", ("identity verification failed at the bureau",)
    if profile.deceased:
        return "restricted", ("the bureau's file for this SSN is flagged deceased",)
    if profile.fraud_score >= HIGH_RISK:
        return "restricted", (f"CRS fraud score {profile.fraud_score} of 100, in its high band",)
    if not profile.bureaus_with_file or not profile.credit_score:
        thin = "no credit file at any bureau" if not profile.bureaus_with_file else "a file, but no score"
        return "starter", (thin, f"identity {'verified' if profile.verified == 0 else 'not fully verified'}")
    if profile.frozen:
        # A frozen file is not a bad file, it is an unreadable one: there is no basis for a full line.
        return "standard", ("a bureau file is frozen, so the person cannot be fully assessed",)
    if profile.bureaus_scored < 3:
        # A file that exists but cannot be scored is not evidence of capacity (CRS's P20).
        return "standard", (f"only {profile.bureaus_scored} of 3 bureaus could score this file",)
    if profile.bureaus_with_file < 3:
        # The bureaus disagree about whether this person exists. Thinner evidence, smaller line.
        return "standard", (f"only {profile.bureaus_with_file} of 3 bureaus hold a file, "
                            f"score {profile.credit_score}",)
    if profile.credit_score < THIN_SCORE:
        # Low capacity is a reason for a small line, never for refusing the agent: this is a
        # spending limit, not a loan. See THIN_SCORE.
        return "starter", (f"score {profile.credit_score}: capacity for a starter line only",)
    if profile.verified == 0 and profile.credit_score >= GOOD_SCORE and profile.risk_level == "low":
        return "full", (f"identity verified, score {profile.credit_score} at "
                        f"{profile.bureaus_with_file} of 3 bureaus, fraud risk {profile.risk_level}",)
    return "standard", (f"identity {'verified' if profile.verified == 0 else 'in review'}, "
                        f"score {profile.credit_score}, fraud risk {profile.risk_level}",)


def trust_line(profile: CrsProfile, requested: Mandate) -> TrustLine:
    """The agent's real limits, from the human's request and the bureau's answer about the human."""
    band, reasons = band_for(profile)
    share, _ = BANDS[band]
    per_payment = max(FLOOR_CENTS, int(requested.per_payment_cap_cents * share))
    daily = max(FLOOR_CENTS, int(requested.daily_cap_cents * share))
    # Never above what the human authorized, whatever the bureau says.
    per_payment = min(per_payment, requested.per_payment_cap_cents)
    daily = min(daily, requested.daily_cap_cents)
    return TrustLine(band=band, per_payment_cap_cents=per_payment, daily_cap_cents=daily,
                     share=share, reasons=reasons)


def apply_trust_line(profile: CrsProfile, mandate: Mandate) -> tuple[Mandate, TrustLine]:
    """The mandate the defense actually enforces: the human's, narrowed to the trust line. Everything
    else about the mandate (the categories, the merchants, the request text) is the human's and is
    left exactly as it was."""
    line = trust_line(profile, mandate)
    narrowed = mandate.model_copy(update={"per_payment_cap_cents": line.per_payment_cap_cents,
                                          "daily_cap_cents": line.daily_cap_cents})
    return narrowed, line

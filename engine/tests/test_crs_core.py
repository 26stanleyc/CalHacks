"""CRS as the identity layer: enrollment, the trust line, the persona conformance suite, and the
promise that with CRS off nothing changes."""

import pytest

from contracts.schemas import IdentityStatus, Mandate
from engine.crs import PERSONAS, MockCrs, PersonaCrs, merge_credit_reports, parse_credit_report
from engine.crs_conformance import BY_DESIGN, run
from engine.crs_enrollment import FAILED, VERIFIED, CrsEnrollment, CrsProfile
from engine.features import CRS_NO_EVIDENCE, FeatureService
from engine.sim.marketplace import MarketplaceWorld
from engine.trust_line import BANDS, FLOOR_CENTS, apply_trust_line, band_for, trust_line

TEAM = "agent1qteam"


def mandate(per_payment=25_000, daily=100_000) -> Mandate:
    return Mandate(mandate_id="md-1", principal_id="p-1", per_payment_cap_cents=per_payment,
                   daily_cap_cents=daily, request_text="buy groceries")


def enrollment(**identities) -> CrsEnrollment:
    on_file = {p for p, s in identities.items() if s != IdentityStatus.MISMATCH}
    return CrsEnrollment(MockCrs(dict(identities), on_file))


# --- enrollment ---------------------------------------------------------------------------------

def test_a_principal_is_resolved_once_and_cached():
    calls = []

    class Counting(MockCrs):
        def identity_verification(self, principal_id, prefill):
            calls.append(principal_id)
            return super().identity_verification(principal_id, prefill)

    e = CrsEnrollment(Counting({"p1": IdentityStatus.VERIFIED}, {"p1"}))
    for _ in range(5):
        e.profile("p1")
    assert calls == ["p1"]


def test_enrollment_costs_at_most_five_products_per_person():
    e = enrollment(p1=IdentityStatus.VERIFIED)
    e.profile("p1")
    assert len(e.audit) <= 5
    assert [row["principal_id"] for row in e.audit] == ["p1"] * len(e.audit)
    assert {row["purpose"] for row in e.audit} == {"agent enrollment: identity of the human behind the agent"}


def test_a_failed_identity_is_never_followed_by_a_credit_pull():
    """A soft pull on an identity the bureau just rejected would be a pull on nobody."""
    e = enrollment(bad=IdentityStatus.MISMATCH)
    profile = e.profile("bad")
    assert profile.verified == FAILED
    assert "credit_report" not in profile.calls
    assert "credit_report" not in {row["product"] for row in e.audit}


def test_crs_being_unreachable_never_allows_a_payment():
    class Down(MockCrs):
        def data_prefill(self, principal_id):
            from engine.crs import CrsUnavailable
            raise CrsUnavailable("sandbox down")

    profile = CrsEnrollment(Down({}, set())).profile("p1")
    assert not profile.available
    # An unavailable profile reads as no evidence, so no CRS rule can fire either way.
    assert band_for(profile)[0] == "starter"


def test_a_forked_enrollment_keeps_the_live_cache_clean():
    """The arena's sandbox resolves its throwaway accounts without touching the live run."""
    e = enrollment(p1=IdentityStatus.VERIFIED)
    e.profile("p1")
    live_audit = len(e.audit)
    fork = e.fork()
    fork.profile("sandbox-account")
    assert "sandbox-account" not in e.profiles and "sandbox-account" in fork.profiles
    assert "p1" in fork.profiles  # it still knows everyone already enrolled
    # The sandbox's own lookups stay in the fork: they are not calls the live run made about a person.
    assert len(e.audit) == live_audit and fork.audit
    assert "sandbox-account" not in {p for members in e.contacts.values() for p in members}


def test_accounts_made_in_bulk_share_a_contact():
    ids = {f"farm{i}": IdentityStatus.MISMATCH for i in range(40)}
    e = CrsEnrollment(MockCrs(ids, set()))
    for p in ids:
        e.profile(p)
    assert max(len(members) for members in e.contacts.values()) >= 3
    assert max(e.principals_sharing_contact(p) for p in ids) >= 3


def test_a_real_person_does_not_share_their_contact():
    ids = {f"real{i}": IdentityStatus.VERIFIED for i in range(40)}
    e = CrsEnrollment(MockCrs(ids, set(ids)))
    for p in ids:
        e.profile(p)
    assert all(e.principals_sharing_contact(p) == 1 for p in ids)


# --- credit reports -----------------------------------------------------------------------------

def test_a_credit_report_reports_trouble_inside_a_200():
    """No file, a freeze and a deceased flag all arrive as HTTP 200, so only the body may decide."""
    assert parse_credit_report({"errorMessages": [{"creditErrorMessageText": "Subject deceased"}]})["status"] == "Deceased"
    assert parse_credit_report({"creditFiles": []})["status"] == "NoFileReturnedNoHit"
    scored = parse_credit_report({"creditFiles": [{"creditFileDetail": "FileReturned"}],
                                  "scores": [{"modelName": "FICO 9", "scoreValue": 742}]})
    assert (scored["status"], scored["score"]) == ("FileReturned", 742)
    unscoreable = parse_credit_report({"creditFiles": [{"creditFileDetail": "FileReturned"}],
                                       "scores": [{"scoreValue": None, "noScoreReasonType": "ScoringNotAvailable"}]})
    assert unscoreable["score"] == 0 and unscoreable["no_score_reason"] == "ScoringNotAvailable"


def test_the_bureaus_are_merged_on_the_median_of_what_came_back():
    merged = merge_credit_reports({"transunion": {"status": "FileReturned", "score": 760},
                                   "equifax": {"status": "NoFileReturnedNoHit", "score": 0},
                                   "experian": {"status": "FileReturned", "score": 700}})
    assert merged["score"] == 760 and merged["bureaus_with_file"] == 2 and not merged["no_file"]


# --- the trust line -----------------------------------------------------------------------------

@pytest.mark.parametrize("band", BANDS)
def test_no_band_ever_raises_the_limit_the_human_asked_for(band):
    """The human's mandate is the ceiling. A good credit file is never a reason to let an agent
    spend more than its owner allowed."""
    small = mandate(per_payment=2_000, daily=4_000)
    for profile in (CrsProfile(verified=VERIFIED, credit_score=820, bureaus_with_file=3, bureaus_scored=3,
                               fraud_score=2),
                    CrsProfile(verified=FAILED), CrsProfile(available=False)):
        narrowed, line = apply_trust_line(profile, small)
        assert narrowed.per_payment_cap_cents <= small.per_payment_cap_cents
        assert narrowed.daily_cap_cents <= small.daily_cap_cents


def test_a_verified_person_with_a_clean_file_gets_the_whole_limit():
    profile = CrsProfile(verified=VERIFIED, credit_score=780, bureaus_with_file=3, bureaus_scored=3, fraud_score=5)
    narrowed, line = apply_trust_line(profile, mandate())
    assert line.band == "full" and narrowed.per_payment_cap_cents == 25_000


def test_a_failed_identity_keeps_a_floor_rather_than_zero():
    narrowed, line = apply_trust_line(CrsProfile(verified=FAILED), mandate())
    assert line.band == "restricted" and narrowed.per_payment_cap_cents == FLOOR_CENTS


def test_an_unassessable_file_does_not_earn_a_full_line():
    frozen = CrsProfile(verified=VERIFIED, credit_score=740, bureaus_with_file=3, bureaus_scored=3,
                        fraud_score=5, frozen=True)
    partial = CrsProfile(verified=VERIFIED, credit_score=740, bureaus_with_file=2, bureaus_scored=2, fraud_score=5)
    assert band_for(frozen)[0] == "standard" and band_for(partial)[0] == "standard"


def test_poor_credit_lowers_the_line_and_never_refuses_the_agent():
    """A spending limit, not a loan: a weak file means less room, not a blocked payment."""
    band, _ = band_for(CrsProfile(verified=VERIFIED, credit_score=540, bureaus_with_file=3, bureaus_scored=3,
                                  fraud_score=10))
    assert band == "starter"
    assert trust_line(CrsProfile(verified=VERIFIED, credit_score=540, bureaus_with_file=3, bureaus_scored=3),
                      mandate()).per_payment_cap_cents > FLOOR_CENTS


def test_high_fraud_risk_restricts_however_good_the_credit_file_is():
    profile = CrsProfile(verified=VERIFIED, credit_score=800, bureaus_with_file=3, bureaus_scored=3, fraud_score=88)
    assert band_for(profile)[0] == "restricted"


# --- the features -------------------------------------------------------------------------------

def test_with_crs_off_every_crs_feature_sits_at_its_no_evidence_value():
    """The promise that keeps every benchmark run before CRS existed valid."""
    service = FeatureService()
    assert service._crs_values("anyone") == CRS_NO_EVIDENCE


def test_an_unreachable_bureau_reads_as_no_evidence_not_as_suspicion():
    class Down(MockCrs):
        def data_prefill(self, principal_id):
            from engine.crs import CrsUnavailable
            raise CrsUnavailable("down")

    service = FeatureService(crs=CrsEnrollment(Down({}, set())))
    assert service._crs_values("p1") == CRS_NO_EVIDENCE


def test_the_identity_lane_sees_the_bureaus_answer_on_a_real_payment():
    service = FeatureService(crs=enrollment(bad=IdentityStatus.MISMATCH))
    values = service._crs_values("bad")
    assert values["i_crs_verified"] == FAILED
    assert values["i_crs_fraud_score"] > CRS_NO_EVIDENCE["i_crs_fraud_score"]


# --- conformance --------------------------------------------------------------------------------

def test_every_identity_persona_lands_where_crs_says_it_should():
    rows = run(live=False)
    assert len(rows) == 20
    unexplained = [r for r in rows if r.verdict == "MISMATCH"]
    assert not unexplained, f"unexplained: {[(r.persona, r.expected, r.band) for r in unexplained]}"


def test_every_deviation_from_crs_is_declared_and_reasoned():
    rows = {r.persona: r for r in run(live=False)}
    for pid, why in BY_DESIGN.items():
        assert pid in rows and len(why) > 40  # a stated reason, not a shrug
        assert not rows[pid].ok  # a persona we actually do answer differently


def test_the_persona_bureau_reproduces_what_crs_publishes():
    """PersonaCrs must answer what CRS says that test person answers, or the offline suite is
    measuring our own invention."""
    for pid, person in PERSONAS.items():
        client = PersonaCrs(pid)
        assert client.identity_verification(pid, None)["result"] == person["identity_expected"]
        assert client.fraud_finder(pid)["risk_score"] == person["fraud_score_expected"]
        merged = client.credit_report(pid)
        expected_scores = [b["score"] for b in person["credit_expected"].values() if b["score"]]
        assert merged["bureaus_scored"] == len(expected_scores)


# --- the engine ---------------------------------------------------------------------------------

def test_the_trust_line_stops_made_up_identities_at_the_mandate_gate():
    """The point of underwriting the mandate: an invented identity is held to a floor before any
    scoring happens, so its payments are over cap rather than merely suspicious."""
    from engine.crs import CrsEscalation
    from engine.engine import FraudEngine

    def over_cap(enrol: bool) -> int:
        world = MarketplaceWorld(seed=42)
        crs = CrsEscalation(MockCrs(world.identities, set(world.identities)))
        engine = FraudEngine(world=world, allowlist={TEAM}, arena=False, hijacker=False, crs=crs,
                             crs_enrollment=enrol)
        engine.run_round(requested_by=TEAM)
        return sum(1 for d in engine.store.decisions if d.vetoed_by and "cap" in d.vetoed_by)

    assert over_cap(True) > over_cap(False)


def test_with_crs_off_the_engine_builds_no_enrollment():
    from engine.engine import FraudEngine

    engine = FraudEngine(world=MarketplaceWorld(seed=42), allowlist={TEAM}, arena=False, hijacker=False)
    assert engine.enrollment is None and engine.pipeline.features.crs is None

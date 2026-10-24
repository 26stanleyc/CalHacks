"""Messages between the engine's uAgents.

uAgents models are pydantic v1, while contracts/schemas.py is pydantic v2, so each message
carries a contracts model as a JSON string and the handlers parse it back.
"""

from uagents import Model


class SubmitTx(Model):
    """Ingress -> orchestrator: a payment to decide. payload = Transaction JSON. commit=False scores
    the payment without changing the defense's state (outside checks)."""

    payload: str
    commit: bool = True


class ScoreRequest(Model):
    """Orchestrator -> specialist. tx = Transaction JSON, features = the lane's FeatureVector JSON."""

    tx: str
    features: str


class VoteReply(Model):
    """Specialist -> orchestrator. payload = Vote JSON."""

    payload: str


class DecisionReply(Model):
    """Orchestrator -> ingress. payload = Decision JSON."""

    payload: str

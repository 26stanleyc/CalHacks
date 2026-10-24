"""Thin wrapper around the Apify API: run an Actor and return its dataset items."""

import os

from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv()


def get_client() -> ApifyClient:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set. Copy .env.example to .env and add your token.")
    return ApifyClient(token)


def run_actor(actor_id: str, run_input: dict, max_items: int | None = None) -> list[dict]:
    """Run an Actor (e.g. "apify/website-content-crawler"), wait for it, and return its results."""
    client = get_client()
    run = client.actor(actor_id).call(run_input=run_input, max_items=max_items)
    if run is None or run.status != "SUCCEEDED":
        raise RuntimeError(f"Actor {actor_id} did not succeed: {run.status if run else 'no run returned'}")
    return list(client.dataset(run.default_dataset_id).iterate_items())


if __name__ == "__main__":
    # Connection check: `uv run python apify_runner.py`
    me = get_client().user().get()
    print(f"Connected to Apify as {me.username}")

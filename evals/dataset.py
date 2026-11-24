"""Build (or refresh) the LangSmith dataset "reviewpilot-planted" from the answer keys.

Each example is one planted PR: inputs {repo, pr}, outputs {bugs, clean_files, neutral_files,
expected_route}, split tuned (#1, #2), seen (#3 to #5) or holdout (#6 onward).
"""

from evals.keys import example as key_example
from evals.keys import load_keys

DATASET = "reviewpilot-planted"
DESCRIPTION = (
    "Planted bug PRs in sa1136/reviewpilot-sandbox with answer keys (evals/planted/prN.md). "
    "Splits: tuned (#1, #2), seen (#3 to #5), holdout (#6 onward). Line tolerance +-1."
)


def sync_dataset(client, name: str = DATASET):
    """Create the dataset if needed and make its examples match the answer keys exactly."""
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(name, description=DESCRIPTION)
    existing = {ex.inputs["pr"]: ex for ex in client.list_examples(dataset_id=dataset.id)}
    created = updated = 0
    for key in load_keys():
        ex = key_example(key)
        if key["pr"] in existing:
            client.update_example(
                existing.pop(key["pr"]).id,
                inputs=ex["inputs"],
                outputs=ex["outputs"],
                metadata=ex["metadata"],
                split=ex["split"],
            )
            updated += 1
        else:
            client.create_example(
                inputs=ex["inputs"],
                outputs=ex["outputs"],
                metadata=ex["metadata"],
                split=ex["split"],
                dataset_id=dataset.id,
            )
            created += 1
    for stale in existing.values():  # an example whose answer key was removed
        client.delete_example(stale.id)
    return dataset, created, updated

"""Taxonomy selection, bounded expansion and validated proposals."""

from difflib import SequenceMatcher

from app.modules.procurement.catalog_interpretation import (
    Option,
    build_questions,
    validate_answers,
)
from app.modules.procurement.normalization import normalize_text


def entry_dict(entry) -> dict:
    return {
        "id": entry.id,
        "attribute": entry.attribute,
        "label": entry.label,
        "aliases": entry.aliases,
        "parent_id": entry.parent_id,
        "status": entry.status,
    }


def shortlist(source: dict, entries: list[dict], limit: int = 8) -> list[dict]:
    name = normalize_text(source["original_name"])
    words = set(name.split())

    def score(entry):
        aliases = [normalize_text(a) for a in [entry["label"], *entry["aliases"]]]
        return max(
            (
                int(" " + alias + " " in " " + name + " "),
                len(words & set(alias.split())),
                SequenceMatcher(None, name, alias).ratio(),
            )
            for alias in aliases
        )

    return sorted(entries, key=lambda e: (score(e), e["id"]), reverse=True)[:limit]


def taxonomy_options(source: dict, entries: list[dict]) -> list[Option]:
    return [
        Option(
            e["id"],
            e["label"],
            source["original_name"],
            "pending_vocabulary" if e["status"] == "proposed" else "vocabulary",
            e["id"],
        )
        for e in entries
    ]


def with_reviewed_matches(entries: list[dict], seed: list[dict]) -> list[dict]:
    """Attach explicitly curated corrections; never learn them from model output."""
    parents = {e["id"]: e["label"] for e in entries if e["attribute"] == "category"}
    mappings = {
        (p["label"], c["label"]): c.get("reviewed_matches", []) for p in seed for c in p["children"]
    }
    return [
        {**e, "reviewed_matches": mappings.get((parents.get(e["parent_id"]), e["label"]), [])}
        for e in entries
    ]


def has_identifiable_name(source: dict, entries: list[dict]) -> bool:
    name = normalize_text(source["original_name"])
    words = [
        w
        for w in name.split()
        if w.isalpha() and len(w) >= 3 and w not in {"con", "sin", "para", "del", "por"}
    ]
    return len(words) >= 2 and any(
        " " + normalize_text(alias) + " " in " " + name + " "
        for e in entries
        for alias in [e["label"], *e["aliases"]]
    )


def validate_proposal(
    proposal, *, source: dict, attribute: str, entries: list[dict], parent_id: str | None
) -> dict:
    base = {"value": None, "origin": "openai_proposal", "evidence": proposal.evidence}
    if proposal.action == "unknown":
        return {**base, "status": "unknown"}
    if proposal.parent_id != parent_id:
        return {**base, "status": "invalid_proposal", "reason": "parent_mismatch"}
    evidence = normalize_text(proposal.evidence)
    if (
        not evidence
        or " " + evidence + " " not in " " + normalize_text(source["original_name"]) + " "
    ):
        return {**base, "status": "invalid_proposal", "reason": "unsupported_evidence"}
    scoped = [e for e in entries if e["attribute"] == attribute and e["parent_id"] == parent_id]
    if proposal.action == "reuse":
        matches = [e for e in scoped if e["id"] == proposal.existing_id]
        if not matches:
            return {**base, "status": "invalid_proposal", "reason": "id_outside_scope"}
    else:
        label = (proposal.label or "").strip()
        aliases = [a.strip() for a in proposal.aliases]
        if (
            not label
            or len(label) > 120
            or any(c.isdigit() for c in label)
            or any(not a or len(a) > 120 for a in aliases)
        ):
            return {**base, "status": "invalid_proposal", "reason": "invalid_label"}
        names = {normalize_text(a) for a in [label, *aliases]}
        matches = [
            e for e in scoped if names & {normalize_text(a) for a in [e["label"], *e["aliases"]]}
        ]
        if not matches:
            similar = [
                e["id"]
                for e in scoped
                if any(
                    SequenceMatcher(None, normalize_text(label), normalize_text(alias)).ratio()
                    >= 0.90
                    for alias in [e["label"], *e["aliases"]]
                )
            ]
            if similar:
                return {
                    **base,
                    "status": "possible_duplicate",
                    "proposed_value": label,
                    "candidate_ids": similar,
                }
            return {
                **base,
                "status": "proposed_new",
                "proposed_value": label,
                "aliases": aliases,
                "parent_id": parent_id,
            }
    if len(matches) != 1:
        return {**base, "status": "possible_duplicate", "candidate_ids": [e["id"] for e in matches]}
    entry = matches[0]
    return {
        **base,
        "status": "interpreted" if entry["status"] == "active" else "proposed_new",
        "value": entry["label"] if entry["status"] == "active" else None,
        "proposed_value": entry["label"],
        "vocabulary_id": entry["id"],
        "parent_id": parent_id,
        "aliases": entry["aliases"],
    }


def resolve_taxonomy(
    source, attribute, entries, *, parent_id, chooser, proposer, threshold, initial_state=None
):
    scoped = [
        e
        for e in entries
        if e["attribute"] == attribute
        and e["parent_id"] == parent_id
        and e["status"] in {"active", "proposed"}
    ]
    initial = shortlist(source, scoped)
    stages = []
    name = " " + normalize_text(source["original_name"]) + " "
    reviewed = [
        (e, phrase)
        for e in scoped
        if e["status"] == "active"
        for phrase in e.get("reviewed_matches", [])
        if " " + normalize_text(phrase) + " " in name
    ]
    if len({e["id"] for e, _ in reviewed}) == 1:
        entry, phrase = reviewed[0]
        conflicting = [
            e["id"]
            for e in scoped
            if e["id"] != entry["id"]
            and any(
                " " + normalize_text(alias) + " " in name for alias in [e["label"], *e["aliases"]]
            )
        ]
        if conflicting:
            return {
                "status": "conflict",
                "value": None,
                "reason": "reviewed_mapping_contradicted",
                "candidate_ids": conflicting,
            }, stages
        return {
            "status": "interpreted",
            "value": entry["label"],
            "vocabulary_id": entry["id"],
            "parent_id": parent_id,
            "evidence": phrase,
            "origin": "reviewed_mapping",
        }, stages
    if len({e["id"] for e, _ in reviewed}) > 1:
        return {"status": "conflict", "value": None, "reason": "reviewed_mapping_conflict"}, stages
    evidence_source = {k: v for k, v in source.items() if k != "category"}
    if parent_id:
        parent = next(e for e in entries if e["id"] == parent_id)
        evidence_source = {**evidence_source, "selected_category": parent["label"]}

    def decide(candidates):
        options = {attribute: taxonomy_options(source, candidates)}
        questions = build_questions(options)
        raw, response = chooser.decide(evidence_source, questions)
        stages.append(
            {
                "provider": "decisions",
                "attribute": attribute,
                "questions": questions,
                "response": raw,
            }
        )
        return validate_answers(response, options, threshold=threshold)[attribute]

    state = initial_state if initial_state is not None else decide(initial)
    uncertain = {"none", "unknown", "low_probability"}
    should_expand = state["status"] in {"none", "low_probability"} or (
        state["status"] == "unknown" and has_identifiable_name(source, entries)
    )
    if should_expand and len(initial) < len(scoped):
        # Bounded complete expansion for the pilot; don't create when unseen options remain.
        expanded = shortlist(source, scoped, limit=100)
        state = decide(expanded)
        if state["status"] in uncertain and len(scoped) > 100:
            return {**state, "status": "low_probability", "reason": "retrieval_incomplete"}, stages
    # This only requests a reviewable proposal; it never activates a concept.
    if state["status"] == "proposed_new" and state.get("vocabulary_id"):
        selected = next(e for e in scoped if e["id"] == state["vocabulary_id"])
        state.update(parent_id=parent_id, aliases=selected["aliases"])
    review_uncertain = state["status"] in {"unknown", "low_probability"} and (
        has_identifiable_name(source, entries)
    )
    if state["status"] == "none" or review_uncertain:
        evidence_source = {
            **evidence_source,
            "resolution_mode": "reuse_only" if review_uncertain else "reuse_or_create",
        }
        try:
            raw, proposal = proposer.propose(evidence_source, attribute, scoped, parent_id)
            stages.append(
                {
                    "provider": "responses",
                    "attribute": attribute,
                    "input": evidence_source,
                    "vocabulary": scoped,
                    "response": raw,
                }
            )
            state = validate_proposal(
                proposal, source=source, attribute=attribute, entries=scoped, parent_id=parent_id
            )
            if review_uncertain and proposal.action == "create":
                state = {
                    "status": "unknown",
                    "value": None,
                    "reason": "uncertainty_does_not_allow_creation",
                }
        except Exception as exc:
            state = {
                "status": "invalid_proposal",
                "value": None,
                "reason": "fallback_failed",
                "error_type": type(exc).__name__,
            }
            stages.append(
                {"provider": "responses", "attribute": attribute, "error_type": type(exc).__name__}
            )
    return state, stages

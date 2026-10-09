import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event, Lock

import pytest

from app.modules.procurement.analysis_contracts import (
    AnalysisMethod,
    ChoiceQuestion,
    EvidenceFragment,
    EvidenceRef,
    IdentityCandidate,
    OfferDecisionRequest,
    OfferInterpretation,
    OfferTextBlock,
)
from app.providers.jev_analysis import (
    JevAnalysisClient,
    JevAnalysisProvider,
    build_classification_questions,
    prepare_block_request,
)


def questions():
    return {
        "identity": {
            "type": "choice",
            "instructions": "Use `offer` independently.",
            "criteria": {"p1": "Product 1", "none": "No match", "insufficient": "Unclear"},
        },
        "basis": {
            "type": "choice",
            "instructions": "Use `offer`, never identity answer.",
            "criteria": {"unit": "Retail item", "pack": "Bundle total", "unknown": "Unclear"},
        },
    }


def response():
    return {
        "model": "jev-1.13.0",
        "answers": {
            key: {
                "type": "choice",
                "choice": next(iter(q["criteria"])),
                "confidence": 0.8,
                "probabilities": {
                    option: int(index == 0) for index, option in enumerate(q["criteria"])
                },
            }
            for key, q in questions().items()
        },
        "usage": {"input_tokens": 123, "output_tokens": 27},
    }


def client_for(body):
    seen = []

    def transport(request, timeout, max_bytes):
        seen.append(json.loads(request.data))
        assert 0 < timeout <= 20
        assert request.full_url == "https://api.typesafe.ai/v1/systemone"
        return body if isinstance(body, bytes) else json.dumps(body).encode()

    return JevAnalysisClient(api_key="test", transport=transport), seen


def test_closed_request_separate_confidence_usage_and_single_call():
    client, seen = client_for(response())
    result = client.evaluate(state={"offer": "Aceite"}, questions=questions())
    assert set(result.answers) == {"identity", "basis"}
    assert result.answers["identity"].confidence == Decimal(".8")
    assert result.answers["identity"].probabilities["p1"] == 1
    assert (result.input_tokens, result.output_tokens) == (123, 27)
    assert client.call_count == len(seen) == 1


@pytest.mark.parametrize("model", ["jev-latest", "jev-preview", "jev", "other-1.2.3", "jev-1"])
def test_reject_unpinned_model_before_transport(model):
    with pytest.raises(ValueError):
        JevAnalysisClient(api_key="test", model=model)


@pytest.mark.parametrize(
    "change",
    [
        {"choice": "foreign"},
        {"type": "score"},
        {"confidence": -1},
        {"confidence": "0.9"},
        {"confidence": True},
        {"confidence": float("nan")},
        {"probabilities": {"p1": -1, "none": 1, "insufficient": 1}},
        {"probabilities": {"p1": 2, "none": 0, "insufficient": 0}},
        {"probabilities": {"p1": 0.5, "none": 0, "insufficient": 0}},
        {"probabilities": {"p1": float("inf"), "none": 0, "insufficient": 0}},
        {"probabilities": {"p1": float("nan"), "none": 0, "insufficient": 0}},
        {"probabilities": {"p1": True, "none": 0, "insufficient": 0}},
        {"probabilities": {"p1": "1", "none": 0, "insufficient": 0}},
        {"probabilities": {"p1": 1}},
        {"probabilities": {"p1": 0, "none": 1, "insufficient": 0}},
    ],
)
def test_invalid_answer_keeps_independent_valid_answer(change):
    body = response()
    body["answers"]["identity"].update(change)
    client, _ = client_for(body)
    result = client.evaluate(state={}, questions=questions())
    assert set(result.answers) == {"basis"}
    assert set(result.errors) == {"identity"}
    assert result.errors["identity"] in {
        "invalid_answer_choice",
        "invalid_answer_type",
        "invalid_answer_schema",
        "invalid_answer_options",
        "invalid_answer_probability_range",
        "invalid_answer_probability_sum",
        "invalid_answer_not_maximum",
    }
    assert result.input_tokens == 123
    assert client.error_count == 1


def test_missing_answer_is_partial_and_tie_is_allowed():
    body = response()
    del body["answers"]["basis"]
    body["answers"]["identity"]["probabilities"] = {"p1": 0.5, "none": 0.5, "insufficient": 0}
    client, _ = client_for(body)
    result = client.evaluate(state={}, questions=questions())
    assert set(result.answers) == {"identity"}
    assert result.errors == {"basis": "missing_answer"}


@pytest.mark.parametrize("kind", ["model", "extra_question", "duplicate", "malformed"])
def test_invalid_envelope_cannot_supply_any_decision(kind):
    body = response()
    if kind == "model":
        body["model"] = "jev-1.12.0"
    elif kind == "extra_question":
        body["answers"]["other_block"] = body["answers"]["basis"]
    elif kind == "duplicate":
        body = b'{"model":"jev-1.13.0","model":"jev-1.13.0","answers":{}}'
    else:
        body = b"not json"
    client, seen = client_for(body)
    result = client.evaluate(state={}, questions=questions())
    assert result.error and not result.answers
    assert len(seen) == 1


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": True, "output_tokens": 0},
        {"input_tokens": -1, "output_tokens": 0},
    ],
)
def test_unknown_usage_is_not_zero(usage):
    body = response()
    body["usage"] = usage
    client, _ = client_for(body)
    result = client.evaluate(state={}, questions=questions())
    assert result.answers and result.input_tokens is result.output_tokens is None


def test_deadline_and_limits_make_no_call():
    client, seen = client_for(response())
    assert client.evaluate(state={}, questions=questions(), deadline=0).error == "deadline_exceeded"
    limited = JevAnalysisClient(api_key="test", max_request_bytes=1)
    assert limited.evaluate(state={}, questions=questions()).error == "request_limit"
    assert not seen and client.call_count == limited.call_count == 0


def test_timeout_is_safe_no_hidden_retry_and_late_usage_survives():
    now = [0.0]

    def transport(request, timeout, max_bytes):
        now[0] = 30
        return json.dumps(response()).encode()

    client = JevAnalysisClient(api_key="SECRET", transport=transport, clock=lambda: now[0])
    result = client.evaluate(state={}, questions=questions())
    assert result.error == "TimeoutError" and not result.answers
    assert result.input_tokens == 123
    assert client.call_count == 1


def test_transport_exception_does_not_expose_key_or_text():
    def transport(*args):
        raise RuntimeError("SECRET and original text")

    client = JevAnalysisClient(api_key="SECRET", transport=transport)
    result = client.evaluate(state={}, questions=questions())
    assert result.error == "RuntimeError"
    assert result.attempted and client.call_count == client.error_count == 1


def evidence():
    return OfferTextBlock(
        block_id="block",
        run_id="run",
        reading_id="reading",
        page_number=1,
        order_on_page=0,
        full_text="Aceite — $100 por unidad",
        fragments=[
            EvidenceFragment(
                fragment_id="f",
                text="Aceite — $100 por unidad",
                order=0,
                legibility="readable",
            )
        ],
    )


def offer_request():
    block = evidence()
    return OfferDecisionRequest(
        business_id="b",
        block=block,
        offer=OfferInterpretation(
            offer_id="offer",
            block_id=block.block_id,
            business_id="b",
            raw_name="Aceite",
            offer_price=100,
            field_evidence={"raw_name": [EvidenceRef(fragment_id="f", quote="Aceite")]},
        ),
        candidates=[
            IdentityCandidate(
                candidate_id="p1", product_id="product", business_id="b", name="Aceite"
            )
        ],
        questions=[
            ChoiceQuestion(
                question_id="offer:identity",
                options=["p1", "none", "insufficient"],
                prompt="ignored",
            ),
            ChoiceQuestion(
                question_id="offer:price_basis",
                options=["unit", "pack", "unknown"],
                prompt="ignored",
            ),
        ],
    )


def answering_client(choose=None):
    seen = []

    def transport(request, timeout, max_bytes):
        body = json.loads(request.data)
        seen.append(body)
        answers = {}
        for key, question in body["questions"].items():
            choice = choose(key, question) if choose else next(iter(question["criteria"]))
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.75,
                "probabilities": {option: int(option == choice) for option in question["criteria"]},
            }
        return json.dumps(
            {
                "model": body["model"],
                "answers": answers,
                "usage": {"input_tokens": 321, "output_tokens": 45},
            }
        ).encode()

    return JevAnalysisClient(api_key="fake", transport=transport), seen


def test_classifies_and_selects_in_one_call_self_contained_questions_metrics():
    client, seen = answering_client()
    provider = JevAnalysisProvider(client=client)
    request = prepare_block_request(business_id="b", block=evidence())
    result = provider.classify_block(request=request)
    assert len(seen) == len(result.calls) == 1
    assert result.calls[0].phase == "classification"
    assert result.calls[0].usage.input_tokens == 321
    assert result.calls[0].cost is None
    assert result.run_id == "run" and result.block_id == "block"
    for key, question in seen[0]["questions"].items():
        assert "block.full_text" in question["instructions"]
        if key.startswith("value:"):
            assert "occurrences" in question["instructions"]
    assert "proposals" not in seen[0]["state"]["block"]


@pytest.mark.parametrize("category", ["mixed", "other", "uncertain"])
def test_non_structured_categories_preserve_text(category):
    client, seen = answering_client(
        lambda key, q: category if key.startswith("fragment:") else next(iter(q["criteria"]))
    )
    result = JevAnalysisProvider(client=client).classify_block(
        request=prepare_block_request(business_id="b", block=evidence()),
    )
    assert result.classifications[0].category == category
    assert seen[0]["state"]["block"]["full_text"] == evidence().full_text


@pytest.mark.parametrize("identity", ["none", "insufficient"])
def test_decisions_preserve_abstentions_and_do_not_mutate_offer(identity):
    client, seen = answering_client(
        lambda key, q: identity if key.endswith("identity") else "unknown"
    )
    provider = JevAnalysisProvider(client=client, analysis_method=AnalysisMethod.OPENAI_STRUCTURED)
    request = offer_request()
    original = request.model_dump()
    result = provider.decide_offer(request=request)
    assert [d.choice for d in result.decisions] == [identity, "unknown"]
    assert request.model_dump() == original
    assert len(seen) == 1 and result.calls[0].analysis_method == "openai_structured"
    assert "ignore price" in seen[0]["questions"]["offer:identity"]["instructions"]


@pytest.mark.parametrize("change", ["tenant", "offer", "candidate", "refs", "options", "duplicate"])
def test_foreign_identity_and_bad_references_rejected_before_http(change):
    client, seen = answering_client()
    request = offer_request()
    if change == "tenant":
        request.business_id = "foreign"
    elif change == "offer":
        request.offer.block_id = "foreign"
    elif change == "candidate":
        request.candidates[0].business_id = "foreign"
    elif change == "refs":
        request.offer.field_evidence["raw_name"][0].fragment_id = "foreign"
    elif change == "options":
        request.questions[0].options.append("foreign")
    else:
        request.candidates.append(request.candidates[0])
    with pytest.raises(ValueError):
        JevAnalysisProvider(client=client).decide_offer(request=request)
    assert not seen


def test_provider_instance_cannot_cross_tenant_or_execution():
    client, seen = answering_client()
    provider = JevAnalysisProvider(client=client)
    request = prepare_block_request(business_id="b", block=evidence())
    provider.classify_block(request=request)
    request.business_id = "other"
    with pytest.raises(ValueError):
        provider.classify_block(request=request)
    request.business_id = "b"
    request.block.run_id = "different-run"
    with pytest.raises(ValueError):
        provider.classify_block(request=request)
    assert len(seen) == 1


def test_unknown_block_question_or_foreign_candidate_rejected():
    client, seen = answering_client()
    request = prepare_block_request(business_id="b", block=evidence())
    request.candidates[0].block_id = "other"
    with pytest.raises(ValueError):
        JevAnalysisProvider(client=client).classify_block(request=request)
    request = prepare_block_request(business_id="b", block=evidence())
    request.questions[0].question_id = "another-block"
    with pytest.raises(ValueError):
        JevAnalysisProvider(client=client).classify_block(request=request)
    assert not seen


def test_call_budget_exhaustion_does_not_retry_or_make_a_call_record():
    def transport(request, timeout, max_bytes):
        return json.dumps(response()).encode()

    client = JevAnalysisClient(api_key="fake", max_calls=1, transport=transport)
    assert client.evaluate(state={}, questions=questions()).attempted
    result = client.evaluate(state={}, questions=questions())
    assert not result.attempted and result.error == "call_budget_exceeded"
    assert client.call_count == 1


def test_next_batch_renews_jev_limit_and_preserves_total_call_count():
    seen = []

    def transport(request, timeout, max_bytes):
        seen.append(request)
        return json.dumps(response()).encode()

    client = JevAnalysisClient(api_key="fake", max_calls=1, transport=transport)
    assert client.evaluate(state={}, questions=questions()).attempted
    assert not client.evaluate(state={}, questions=questions()).attempted
    provider = JevAnalysisProvider(client=client)
    provider.start_batch()
    assert client.evaluate(state={}, questions=questions()).attempted
    assert not client.evaluate(state={}, questions=questions()).attempted
    assert len(seen) == client.call_count == 2


def test_concurrency_limit_shared_by_workers():
    entered, release = Event(), Event()
    lock = Lock()
    active = [0, 0]

    def transport(request, timeout, max_bytes):
        with lock:
            active[0] += 1
            active[1] = max(active)
            if active[0] == 2:
                entered.set()
        try:
            assert release.wait(3)
            return json.dumps(response()).encode()
        finally:
            with lock:
                active[0] -= 1

    client = JevAnalysisClient(api_key="fake", max_concurrency=2, transport=transport)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(client.evaluate, state={}, questions=questions()) for _ in range(4)]
        try:
            assert entered.wait(3)
        finally:
            release.set()
        assert all(future.result().answers for future in futures)
    assert active[1] == 2 and client.call_count == 4


@pytest.mark.parametrize("kind", ["timeout", "refusal", "partial", "invalid_probability"])
def test_domain_adapter_always_records_paid_attempts_as_safe_results(kind):
    def transport(request, timeout, max_bytes):
        body = json.loads(request.data)
        if kind == "timeout":
            raise TimeoutError("secret payload")
        if kind == "refusal":
            return json.dumps(
                {
                    "model": body["model"],
                    "refusal": "no",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                }
            ).encode()
        answers = {}
        for key, question in body["questions"].items():
            choice = next(iter(question["criteria"]))
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.8,
                "probabilities": {option: int(option == choice) for option in question["criteria"]},
            }
        if kind == "partial":
            answers.pop("offer:price_basis")
        else:
            answers["offer:identity"]["probabilities"]["p1"] = -1
        return json.dumps(
            {
                "model": body["model"],
                "answers": answers,
                "usage": {"input_tokens": 5, "output_tokens": 1},
            }
        ).encode()

    client = JevAnalysisClient(api_key="fake", transport=transport)
    provider = JevAnalysisProvider(client=client)
    result = provider.decide_offer(request=offer_request())
    assert len(result.calls) == client.call_count == 1
    assert result.calls[0].status in {"error", "incomplete"}
    assert result.calls[0].safe_error and result.reason_codes
    assert "secret" not in result.model_dump_json()
    if kind != "timeout":
        assert result.calls[0].usage.input_tokens == 5
    if kind == "partial":
        assert [d.question_id for d in result.decisions] == ["offer:identity"]


def test_public_builder_uses_existing_backend_ids_and_rejects_foreign_offer_question():
    request = prepare_block_request(business_id="b", block=evidence())
    for i, candidate in enumerate(request.candidates):
        candidate.candidate_id = f"backend-{i}"
    questions = build_classification_questions(block=request.block, candidates=request.candidates)
    assert any("backend-0" in q.options for q in questions)
    new_request = prepare_block_request(
        business_id="b",
        block=request.block,
        candidates=request.candidates,
    )
    assert new_request.candidates == request.candidates and new_request.questions == questions
    client, seen = answering_client()
    wrong = offer_request()
    wrong.questions[0].question_id = "foreign-offer:identity"
    with pytest.raises(ValueError):
        JevAnalysisProvider(client=client).decide_offer(request=wrong)
    assert not seen


def test_compact_wire_keeps_rules_once_and_options_resolve_to_exact_state():
    client, seen = answering_client()
    provider = JevAnalysisProvider(client=client)
    request = prepare_block_request(business_id="b", block=evidence())
    provider.classify_block(request=request)
    assert seen
    wire = seen[0]
    assert wire["state"]["policy_rules"] == provider._fragment_prompt
    assert all(
        question["instructions"].startswith("Apply state.policy_rules.")
        for question in wire["questions"].values()
    )
    assert all(
        description is None
        for q in wire["questions"].values()
        for description in q["criteria"].values()
    )
    assert wire["state"]["occurrences"]


def test_question_partitions_reserve_only_sent_posts_and_keep_all_valid_answers():
    client, seen = answering_client()
    client._question_limit = 2
    provider = JevAnalysisProvider(client=client)
    request = prepare_block_request(business_id="b", block=evidence())
    count = [0]

    def reserve():
        count[0] += 1
        return True

    assert provider.preflight(request=request) is None
    assert not seen and count == [0]
    result = provider.classify_block(request=request, reserve_call=reserve)
    assert len(seen) == count[0] == len(result.calls) > 1
    assert all(len(payload["questions"]) <= 2 for payload in seen)
    assert {d.question_id for d in result.decisions} == {q.question_id for q in request.questions}


def test_oversized_shared_state_is_rejected_before_reservation_or_transport():
    client, seen = answering_client()
    client._request_limit = 10
    provider = JevAnalysisProvider(client=client)
    request = prepare_block_request(business_id="b", block=evidence())
    local = provider.preflight(request=request)
    assert local and not local.calls and local.reason_codes
    result = provider.classify_block(
        request=request, reserve_call=lambda: pytest.fail("Local rejection must not reserve")
    )
    assert not result.calls and not seen and client.call_count == 0


def test_configurable_acceptance_thresholds_travel_in_jev_state():
    provider = JevAnalysisProvider(client=object())
    expected = {"allow_jev_acceptance": True, "identity_probability": .85,
                "price_probability": .91, "confidence": .88, "margin": .10}
    provider.acceptance_thresholds = expected
    state, _ = provider._wire(state={"offer": "evidence"}, questions=[], prompt="rules")
    assert state["acceptance_thresholds"] == expected
    assert state["acceptance_thresholds"] is not expected
    assert state["offer"] == "evidence"

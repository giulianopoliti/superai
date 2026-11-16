"""Deterministic proposal/review lifecycle. Never writes original products or families."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.db.models import (
    CatalogFamilyChangeSuggestionModel as Change,
)
from app.db.models import (
    CatalogFamilyCreationSuggestionModel as Creation,
)
from app.db.models import (
    CatalogFamilySuggestionEventModel as Event,
)
from app.db.models import (
    CatalogInterpretationModel,
    ProductFamilyModel,
    ProductModel,
    UserModel,
    utc_now,
)
from app.db.repositories.family_suggestions import get_suggestion, lock_business
from app.modules.procurement.catalog_interpretation import digest, snapshot
from app.modules.procurement.normalization import normalize_text

REVIEW_ROLES = {"owner", "admin", "manager"}


def family_snapshot(product):
    return {
        **snapshot(product),
        "family_id": product.family_id,
        "external_family_id": product.external_family_id,
    }


def stable_id(*parts):
    return str(uuid5(NAMESPACE_URL, "/".join(str(p) for p in parts)))


def _event(session, row, kind, action, before, reason, actor=None, key=None):
    key = key or f"{kind}/{row.id}/{action}"
    existing = session.scalar(
        select(Event).where(Event.business_id == row.business_id, Event.idempotency_key == key)
    )
    if existing:
        return
    session.add(
        Event(
            id=stable_id(row.business_id, key),
            business_id=row.business_id,
            change_suggestion_id=row.id if kind == "change" else None,
            creation_suggestion_id=row.id if kind == "creation" else None,
            event_type=action,
            actor_type="user" if actor else "system",
            actor_id=actor,
            before=before,
            after={"status": row.status},
            reason=reason,
            idempotency_key=key,
        )
    )


class FamilySuggestionService:
    def __init__(self, session, business_id):
        self.session = session
        self.business_id = business_id

    def family(self, identifier):
        row = self.session.scalar(
            select(ProductFamilyModel).where(
                ProductFamilyModel.business_id == self.business_id,
                ProductFamilyModel.id == identifier,
                ProductFamilyModel.active.is_(True),
            )
        )
        if row is None:
            raise ValueError("Family does not belong to business or is inactive")
        return row

    def family_named(self, label):
        matches = [
            f
            for f in self.session.scalars(
                select(ProductFamilyModel).where(
                    ProductFamilyModel.business_id == self.business_id,
                    ProductFamilyModel.active.is_(True),
                )
            ).all()
            if normalize_text(f.name) == normalize_text(label)
        ]
        if len(matches) != 1:
            raise ValueError("Family name is missing or ambiguous; resolve by ID")
        return matches[0]

    def product(self, identifier):
        row = self.session.scalar(
            select(ProductModel)
            .where(ProductModel.business_id == self.business_id, ProductModel.id == identifier)
            .with_for_update()
        )
        if row is None or not row.active:
            raise ValueError("Product does not belong to business or is inactive")
        if row.family_id:
            self.family(row.family_id)
        return row

    def context(self, request):
        product = self.product(request.product_id)
        interpretation = self.session.scalar(
            select(CatalogInterpretationModel).where(
                CatalogInterpretationModel.business_id == self.business_id,
                CatalogInterpretationModel.product_id == request.product_id,
                CatalogInterpretationModel.id == request.interpretation_id,
            )
        )
        if interpretation is None:
            raise ValueError("Interpretation does not belong to business/product")
        if interpretation.source != snapshot(product):
            raise ValueError("Interpretation is stale")
        evidence = normalize_text(request.evidence)
        if not evidence or " " + evidence + " " not in " " + normalize_text(product.name) + " ":
            raise ValueError("Evidence is not a fragment of the original name")
        if not request.reason.strip():
            raise ValueError("A concrete reason is required")
        return product, interpretation

    def propose_creation(self, request):
        lock_business(self.session, self.business_id)
        product, interpretation = self.context(request)
        name = normalize_text(request.proposed_name)
        if (
            not name
            or not request.description.strip()
            or any(not x.strip() for x in request.includes + request.excludes)
        ):
            raise ValueError("Invalid family definition")
        observed = self.session.scalars(
            select(ProductFamilyModel).where(
                ProductFamilyModel.business_id == self.business_id,
                ProductFamilyModel.active.is_(True),
            )
        ).all()
        if any(normalize_text(f.name) == name for f in observed):
            raise ValueError("An observed family already has this name")
        definition = digest(
            {
                "description": normalize_text(request.description),
                "includes": sorted(normalize_text(x) for x in request.includes),
                "excludes": sorted(normalize_text(x) for x in request.excludes),
            }
        )
        previous = self.session.scalars(
            select(Creation)
            .where(Creation.business_id == self.business_id, Creation.normalized_name == name)
            .order_by(Creation.created_at.desc())
        ).all()
        for row in previous:
            if row.status in {"pending", "approved"}:
                if row.definition_hash != definition:
                    raise ValueError("Conflicting definition for an open family proposal")
                return row, "reused"
            if row.status == "rejected" and row.definition_hash == definition:
                return row, "rejected"
        current = family_snapshot(product)
        identifier = stable_id(self.business_id, "creation", name, definition, digest(current))
        for previous_row in previous:
            if previous_row.id == identifier:
                return previous_row, previous_row.status
        row = Creation(
            id=identifier,
            business_id=self.business_id,
            status="pending",
            proposed_name=request.proposed_name.strip(),
            normalized_name=name,
            description=request.description.strip(),
            includes=request.includes,
            excludes=request.excludes,
            definition_hash=definition,
            reason=request.reason,
            evidence={"text": request.evidence, "interpretation_id": interpretation.id},
            evaluation_metadata={"source_product_id": product.id, "source_snapshot": current},
            policy_version=request.policy_version,
        )
        self.session.add(row)
        self.session.flush()
        _event(self.session, row, "creation", "proposed", {}, request.reason)
        return row, "inserted"

    def propose_change(self, request):
        lock_business(self.session, self.business_id)
        product, interpretation = self.context(request)
        if request.target_family_id:
            target = self.family(request.target_family_id)
            if product.family_id == target.id or (
                not product.family_id
                and normalize_text(product.category) == normalize_text(target.name)
            ):
                raise ValueError("Product already belongs to target family")
        else:
            creation = get_suggestion(
                self.session, self.business_id, "creation", request.target_creation_suggestion_id
            )
            if creation.status not in {"pending", "approved", "applied"}:
                raise ValueError("Target creation is not available")
        current = family_snapshot(product)
        source_hash = digest(current)
        rows = self.session.scalars(
            select(Change)
            .where(Change.business_id == self.business_id, Change.product_id == product.id)
            .order_by(Change.created_at.desc())
        ).all()

        def same_target(r):
            return (
                r.target_family_id == request.target_family_id
                and r.target_creation_suggestion_id == request.target_creation_suggestion_id
            )

        for row in rows:
            if row.status in {"pending", "approved"}:
                if row.source_hash != source_hash:
                    self._stale(row, "change", "El producto cambió desde la propuesta")
                    self.session.flush()
                elif same_target(row):
                    if row.interpretation_id != interpretation.id:
                        _event(
                            self.session,
                            row,
                            "change",
                            "evidence_observed",
                            {"interpretation_id": interpretation.id, "text": request.evidence},
                            request.reason,
                            key=f"change/{row.id}/evidence/{interpretation.id}",
                        )
                    return row, "reused"
                else:
                    raise ValueError("A different proposal is already open; human review required")
            if row.status == "rejected" and row.source_hash == source_hash and same_target(row):
                return row, "rejected"
            if row.status == "stale" and row.source_hash == source_hash and same_target(row):
                return row, "stale"
        identifier = stable_id(
            self.business_id,
            "change",
            product.id,
            source_hash,
            request.target_family_id or request.target_creation_suggestion_id,
        )
        row = Change(
            id=identifier,
            business_id=self.business_id,
            product_id=product.id,
            interpretation_id=interpretation.id,
            original_family_id=product.family_id,
            original_family_name=product.category,
            target_family_id=request.target_family_id,
            target_creation_suggestion_id=request.target_creation_suggestion_id,
            source_hash=source_hash,
            source_snapshot=current,
            status="pending",
            reason=request.reason,
            evidence={"text": request.evidence},
            evaluation_metadata=interpretation.response.get("original_family_validation", {}),
            policy_version=request.policy_version,
        )
        self.session.add(row)
        self.session.flush()
        _event(self.session, row, "change", "proposed", {}, request.reason)
        return row, "inserted"

    def _stale(self, row, kind, reason):
        before = {"status": row.status}
        row.status = "stale"
        _event(self.session, row, kind, "stale", before, reason)

    def _actor(self, actor_id):
        actor = self.session.scalar(
            select(UserModel).where(
                UserModel.business_id == self.business_id, UserModel.id == actor_id
            )
        )
        if actor is None or actor.role not in REVIEW_ROLES:
            raise ValueError("Review requires an owner, admin or manager of this business")

    def _observed_change(self, row, product):
        target_id = row.target_family_id
        if not target_id:
            creation = get_suggestion(
                self.session, self.business_id, "creation", row.target_creation_suggestion_id
            )
            if creation.status != "applied":
                return False
            target_id = creation.created_family_id
        target = self.family(target_id)
        ignored = {"category", "family_id", "external_family_id"}
        unchanged = {k: v for k, v in family_snapshot(product).items() if k not in ignored} == {
            k: v for k, v in row.source_snapshot.items() if k not in ignored
        }
        return (
            unchanged
            and product.family_id == target.id
            and normalize_text(product.category) == normalize_text(target.name)
        )

    def review(self, kind, identifier, decision, actor_id, notes):
        lock_business(self.session, self.business_id)
        self._actor(actor_id)
        if decision not in {"approved", "rejected"} or not notes.strip():
            raise ValueError("A review decision and notes are required")
        row = get_suggestion(self.session, self.business_id, kind, identifier)
        if row.status == decision:
            return row
        if row.status != "pending":
            raise ValueError("Only pending suggestions can be reviewed")
        if kind == "change":
            product = self.product(row.product_id)
            if digest(family_snapshot(product)) != row.source_hash:
                self._stale(row, kind, "La revisión encontró datos desactualizados")
                return row
            if row.target_family_id:
                self.family(row.target_family_id)
            else:
                creation = get_suggestion(
                    self.session, self.business_id, "creation", row.target_creation_suggestion_id
                )
                if creation.status not in {"pending", "approved", "applied"}:
                    self._stale(row, kind, "La creación de destino ya no está disponible")
                    return row
        elif decision == "approved":
            observed = self.session.scalars(
                select(ProductFamilyModel).where(
                    ProductFamilyModel.business_id == self.business_id,
                    ProductFamilyModel.active.is_(True),
                )
            ).all()
            if any(normalize_text(f.name) == row.normalized_name for f in observed):
                raise ValueError("Family now exists; review destination before approving creation")
            children = self.session.scalars(
                select(Change).where(
                    Change.business_id == self.business_id,
                    Change.target_creation_suggestion_id == row.id,
                    Change.status.in_(["pending", "approved"]),
                )
            ).all()
            valid = False
            for child in children:
                if digest(family_snapshot(self.product(child.product_id))) != child.source_hash:
                    self._stale(child, "change", "Producto desactualizado en la creación")
                else:
                    valid = True
            if not valid:
                self._stale(row, kind, "La creación no tiene movimientos vigentes")
                return row
        before = {"status": row.status}
        row.status, row.reviewed_by, row.reviewed_at = decision, actor_id, utc_now()
        row.review_notes = notes
        if kind == "creation" and decision == "rejected":
            children = self.session.scalars(
                select(Change).where(
                    Change.business_id == self.business_id,
                    Change.target_creation_suggestion_id == row.id,
                    Change.status.in_(["pending", "approved"]),
                )
            ).all()
            for child in children:
                self._stale(child, "change", "La propuesta de creación fue rechazada")
        _event(self.session, row, kind, decision, before, notes, actor_id)
        self.session.flush()
        return row

    def check_stale(self):
        lock_business(self.session, self.business_id)
        rows = self.session.scalars(
            select(Change).where(
                Change.business_id == self.business_id, Change.status.in_(["pending", "approved"])
            )
        ).all()
        changed = []
        for row in rows:
            try:
                product = self.product(row.product_id)
                fresh = digest(family_snapshot(product)) == row.source_hash
                if not fresh and row.status == "approved" and self._observed_change(row, product):
                    # Keep approval pending explicit reconciliation with an import reference.
                    fresh = True
                if row.target_family_id:
                    self.family(row.target_family_id)
                else:
                    target = get_suggestion(
                        self.session,
                        self.business_id,
                        "creation",
                        row.target_creation_suggestion_id,
                    )
                    fresh = fresh and target.status in {"pending", "approved", "applied"}
            except ValueError:
                fresh = False
            if not fresh:
                self._stale(row, "change", "La fuente o el destino cambiaron")
                changed.append(row.id)
        return changed

    def reconcile(self, kind, identifier, actor_id, reference, observed_family_id=None):
        """Record a verified observation; never changes the source catalog."""
        lock_business(self.session, self.business_id)
        self._actor(actor_id)
        if not reference.strip():
            raise ValueError("An application/import reference is required")
        row = get_suggestion(self.session, self.business_id, kind, identifier)
        if row.status == "applied":
            return row
        if row.status != "approved":
            raise ValueError("Only approved suggestions can be reconciled")
        if kind == "creation":
            observed = self.family(observed_family_id)
            if normalize_text(observed.name) != row.normalized_name:
                raise ValueError("Observed family name does not match approved creation")
            row.created_family_id = observed.id
        else:
            target_id = row.target_family_id
            if not target_id:
                creation = get_suggestion(
                    self.session, self.business_id, "creation", row.target_creation_suggestion_id
                )
                if creation.status != "applied":
                    raise ValueError("Family creation has not been confirmed")
                target_id = creation.created_family_id
            observed = self.family(target_id)
            product = self.product(row.product_id)
            current = family_snapshot(product)
            ignored = {"category", "family_id", "external_family_id"}
            if {k: v for k, v in current.items() if k not in ignored} != {
                k: v for k, v in row.source_snapshot.items() if k not in ignored
            }:
                self._stale(row, kind, "Otros datos del producto cambiaron durante la aplicación")
                return row
            if product.family_id != observed.id or normalize_text(
                product.category
            ) != normalize_text(observed.name):
                raise ValueError("Current catalog does not confirm the approved movement")
        before = {"status": row.status}
        row.status, row.applied_at, row.application_reference = "applied", utc_now(), reference
        _event(self.session, row, kind, "applied", before, reference, actor_id)
        self.session.flush()
        return row

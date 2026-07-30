import asyncio
import json
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.assistant.engine import AssistantEngine
from app.assistant.intent_router import IntentRouter
from app.assistant.llm_intent_router import LLMIntentRouter
from app.channels.whatsapp_kapso import (
    KapsoWebhookError,
    KapsoWhatsAppAdapter,
    verify_kapso_signature,
)
from app.db.repositories.conversations import InMemoryConversationMessageRepository
from app.db.repositories.reminders import InMemoryReminderRepository
from app.db.repositories.sql_conversations import SqlConversationMessageRepository
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_reminders import SqlReminderRepository
from app.db.session import SessionLocal
from app.modules.procurement.catalog_import import PosCatalogImportService
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.review import ProductMatchReviewService
from app.modules.procurement.schemas import (
    CatalogImportPathRequest,
    CatalogImportResult,
    ProductMatchCorrectionRequest,
    ProductMatchFeedback,
    ProductMatchReviewList,
    ProductMatchReviewRequest,
    SupplierOfferCompareRequest,
    SupplierOfferCompareResponse,
    SupplierOfferDocument,
    SupplierOfferDocumentAnalysisResponse,
    SupplierOfferImportResult,
    SupplierOfferJsonPathRequest,
)
from app.modules.procurement.supplier_offers import SupplierOfferService
from app.modules.reminders.dispatcher import ReminderDispatcher
from app.modules.reminders.service import ReminderService
from app.providers.documents.gemini import GeminiSupplierOfferDocumentProvider
from app.providers.documents.local_text import LocalTextSupplierOfferProvider
from app.providers.documents.openai import OpenAISupplierOfferDocumentProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.gemini import GeminiLLMProvider
from app.providers.llm.openai import OpenAILLMProvider
from app.providers.notifications.kapso import KapsoNotificationProvider
from app.schemas.assistant import AssistantRequest, AssistantResponse
from app.settings import settings


def build_repositories():
    if SessionLocal is not None:
        return SqlReminderRepository(SessionLocal), SqlConversationMessageRepository(SessionLocal)
    return InMemoryReminderRepository(), InMemoryConversationMessageRepository()


def build_procurement_repository() -> SqlProcurementRepository:
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")
    return SqlProcurementRepository(SessionLocal)


def build_llm_provider() -> LLMProvider | None:
    if not settings.llm_enabled:
        return None

    provider = settings.llm_provider.strip().lower()
    if provider == "gemini" and settings.gemini_api_key:
        return GeminiLLMProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider == "openai" and settings.openai_api_key:
        return OpenAILLMProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return None


def build_supplier_offer_document_provider(provider_name: str):
    provider = provider_name.strip().lower()
    if provider == "local_text":
        return LocalTextSupplierOfferProvider()
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured.")
        return GeminiSupplierOfferDocumentProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_document_model or settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider == "openai":
        if not settings.openai_api_key:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")
        return OpenAISupplierOfferDocumentProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise HTTPException(status_code=400, detail=f"Unsupported extraction provider: {provider_name}")


def build_engine(
    reminder_repository=None,
    conversation_repository=None,
) -> AssistantEngine:
    if reminder_repository is None or conversation_repository is None:
        reminder_repository, conversation_repository = build_repositories()

    reminder_service = ReminderService(reminder_repository)
    intent_router = IntentRouter()
    llm_provider = build_llm_provider()
    if llm_provider is not None:
        intent_router = LLMIntentRouter(
            llm_provider=llm_provider,
            fallback_router=intent_router,
        )
    return AssistantEngine(
        intent_router=intent_router,
        reminder_service=reminder_service,
        conversation_repository=conversation_repository,
    )


def create_app() -> FastAPI:
    reminder_repository, conversation_repository = build_repositories()
    reminder_service = ReminderService(reminder_repository)
    engine = build_engine(reminder_repository, conversation_repository)
    reminder_dispatcher = ReminderDispatcher(
        reminder_service=reminder_service,
        notification_provider=KapsoNotificationProvider(
            api_key=settings.kapso_api_key,
            phone_number_id=settings.kapso_sandbox_phone_number_id,
        ),
    )
    kapso_adapter = KapsoWhatsAppAdapter(
        business_id=settings.default_business_id,
        api_key=settings.kapso_api_key,
        phone_number_id=settings.kapso_sandbox_phone_number_id,
    )
    scheduler_task: asyncio.Task[None] | None = None

    async def scheduler_loop() -> None:
        while True:
            reminder_dispatcher.dispatch_due()
            await asyncio.sleep(settings.scheduler_interval_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal scheduler_task
        if settings.scheduler_enabled:
            scheduler_task = asyncio.create_task(scheduler_loop())
        try:
            yield
        finally:
            if scheduler_task is not None:
                scheduler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler_task

    api = FastAPI(title=settings.app_name, lifespan=lifespan)

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/assistant/message", response_model=AssistantResponse)
    def assistant_message(request: AssistantRequest) -> AssistantResponse:
        return engine.handle_message(request)

    @api.post("/internal/reminders/dispatch-due")
    def dispatch_due_reminders(
        x_internal_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        if settings.internal_api_token and x_internal_token != settings.internal_api_token:
            raise HTTPException(status_code=401, detail="Invalid internal token.")

        results = reminder_dispatcher.dispatch_due()
        return {
            "processed": len(results),
            "results": [
                {
                    "reminder_id": result.reminder_id,
                    "status": result.status,
                    "notification_status": result.notification_status,
                    "metadata": result.metadata,
                }
                for result in results
            ],
        }

    @api.post("/procurement/catalog-imports", response_model=CatalogImportResult)
    def import_catalog(request: CatalogImportPathRequest) -> CatalogImportResult:
        csv_path = Path(request.csv_path)
        if not csv_path.exists():
            raise HTTPException(status_code=400, detail="CSV file not found.")

        service = PosCatalogImportService(build_procurement_repository())
        return service.import_csv(business_id=request.business_id, csv_path=csv_path)

    @api.post("/procurement/catalog-imports/from-file", response_model=CatalogImportResult)
    async def import_catalog_from_file(
        file: Annotated[UploadFile, File()],
        business_id: Annotated[str, Form()] = settings.default_business_id,
    ) -> CatalogImportResult:
        if not file.filename:
            raise HTTPException(status_code=400, detail="filename is required.")

        service = PosCatalogImportService(build_procurement_repository())
        with tempfile.TemporaryDirectory(prefix="stock-ai-catalog-") as temp_dir:
            csv_path = Path(temp_dir) / Path(file.filename).name
            with csv_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            return service.import_csv(business_id=business_id, csv_path=csv_path)

    @api.post(
        "/procurement/supplier-offers/from-json",
        response_model=SupplierOfferImportResult,
    )
    def import_supplier_offer_from_json(
        request: SupplierOfferJsonPathRequest,
    ) -> SupplierOfferImportResult:
        json_path = Path(request.json_path)
        if not json_path.exists():
            raise HTTPException(status_code=400, detail="Supplier offer JSON file not found.")

        with json_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Supplier offer JSON must be an object.")
        if not isinstance(payload.get("supplier_name"), str):
            raise HTTPException(status_code=400, detail="supplier_name is required.")
        if not isinstance(payload.get("items"), list):
            raise HTTPException(status_code=400, detail="items must be a list.")

        service = SupplierOfferService(build_procurement_repository())
        return service.create_manual_offer(
            business_id=request.business_id,
            supplier_name=payload["supplier_name"],
            source_filename=str(payload.get("source_filename") or json_path.name),
            raw_text=payload.get("raw_text") if isinstance(payload.get("raw_text"), str) else None,
            items=payload["items"],
        )

    @api.get(
        "/procurement/supplier-offers",
        response_model=list[SupplierOfferDocument],
    )
    def list_supplier_offer_documents(
        business_id: str = settings.default_business_id,
        limit: int = 20,
    ) -> list[SupplierOfferDocument]:
        repository = build_procurement_repository()
        return repository.list_supplier_offer_documents(
            business_id=business_id,
            limit=min(max(limit, 1), 100),
        )

    @api.post(
        "/procurement/supplier-offers/from-document",
        response_model=SupplierOfferDocumentAnalysisResponse,
    )
    async def import_supplier_offer_from_document(
        file: Annotated[UploadFile, File()],
        business_id: Annotated[str, Form()] = settings.default_business_id,
        supplier_name: Annotated[str | None, Form()] = None,
        extraction_provider: Annotated[str, Form()] = "gemini",
        max_candidates: Annotated[int, Form()] = 1,
        persist_candidates: Annotated[bool, Form()] = True,
    ) -> SupplierOfferDocumentAnalysisResponse:
        if not file.filename:
            raise HTTPException(status_code=400, detail="filename is required.")

        provider = build_supplier_offer_document_provider(extraction_provider)
        repository = build_procurement_repository()
        supplier_offer_service = SupplierOfferService(repository)
        match_service = ProductMatchService(repository)

        with tempfile.TemporaryDirectory(prefix="stock-ai-offer-") as temp_dir:
            document_path = Path(temp_dir) / Path(file.filename).name
            with document_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)

            try:
                extraction = provider.extract_supplier_offer(
                    document_path=document_path,
                    supplier_hint=supplier_name,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Document extraction failed: {exc}",
                ) from exc

        resolved_supplier_name = extraction.supplier_name or supplier_name
        if not resolved_supplier_name:
            raise HTTPException(
                status_code=422,
                detail="Supplier name could not be extracted. Send supplier_name.",
            )
        if not extraction.items:
            raise HTTPException(status_code=422, detail="No supplier offer items were extracted.")

        import_result = supplier_offer_service.create_offer_from_extraction(
            business_id=business_id,
            supplier_name=resolved_supplier_name,
            extraction=extraction,
        )
        report = match_service.compare_supplier_offer(
            business_id=business_id,
            supplier_offer_document_id=import_result.document.id,
            max_candidates_per_item=max_candidates,
        )
        persisted_count = 0
        if persist_candidates:
            persisted_count = len(match_service.save_supplier_offer_candidates(report=report))
        return SupplierOfferDocumentAnalysisResponse(
            import_result=import_result,
            comparison=SupplierOfferCompareResponse(
                report=report,
                persisted_count=persisted_count,
            ),
            extraction_warnings=extraction.warnings,
        )

    @api.post(
        "/procurement/supplier-offers/{supplier_offer_document_id}/compare",
        response_model=SupplierOfferCompareResponse,
    )
    def compare_supplier_offer(
        supplier_offer_document_id: str,
        request: SupplierOfferCompareRequest,
    ) -> SupplierOfferCompareResponse:
        service = ProductMatchService(build_procurement_repository())
        report = service.compare_supplier_offer(
            business_id=request.business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            max_candidates_per_item=request.max_candidates,
        )
        persisted_count = 0
        if request.persist_candidates:
            persisted_count = len(service.save_supplier_offer_candidates(report=report))
        return SupplierOfferCompareResponse(report=report, persisted_count=persisted_count)

    @api.get(
        "/procurement/supplier-offers/{supplier_offer_document_id}/matches",
        response_model=ProductMatchReviewList,
    )
    def list_supplier_offer_matches(
        supplier_offer_document_id: str,
        business_id: str = settings.default_business_id,
    ) -> ProductMatchReviewList:
        service = ProductMatchReviewService(build_procurement_repository())
        return service.list_candidates(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
        )

    @api.post(
        "/procurement/product-matches/{product_match_candidate_id}/accept",
        response_model=ProductMatchFeedback,
    )
    def accept_product_match(
        product_match_candidate_id: str,
        request: ProductMatchReviewRequest,
    ) -> ProductMatchFeedback:
        service = ProductMatchReviewService(build_procurement_repository())
        try:
            return service.accept_candidate(
                product_match_candidate_id=product_match_candidate_id,
                request=request,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post(
        "/procurement/product-matches/{product_match_candidate_id}/reject",
        response_model=ProductMatchFeedback,
    )
    def reject_product_match(
        product_match_candidate_id: str,
        request: ProductMatchReviewRequest,
    ) -> ProductMatchFeedback:
        service = ProductMatchReviewService(build_procurement_repository())
        try:
            return service.reject_candidate(
                product_match_candidate_id=product_match_candidate_id,
                request=request,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post(
        "/procurement/product-matches/{product_match_candidate_id}/correct",
        response_model=ProductMatchFeedback,
    )
    def correct_product_match(
        product_match_candidate_id: str,
        request: ProductMatchCorrectionRequest,
    ) -> ProductMatchFeedback:
        service = ProductMatchReviewService(build_procurement_repository())
        try:
            return service.correct_candidate(
                product_match_candidate_id=product_match_candidate_id,
                request=request,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/webhooks/kapso")
    async def kapso_webhook(
        request: Request,
        x_webhook_signature: str | None = Header(default=None),
        x_webhook_event: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        raw_body = await request.body()
        if not verify_kapso_signature(
            raw_body,
            x_webhook_signature,
            settings.kapso_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail="Invalid Kapso webhook signature.")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Kapso webhook payload must be an object.")

        if x_webhook_event and x_webhook_event != "whatsapp.message.received":
            return {"processed": 0, "event": x_webhook_event, "status": "ignored"}

        try:
            assistant_requests = kapso_adapter.to_assistant_requests(payload)
        except KapsoWebhookError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        replies: list[str] = []
        outbound_deliveries: list[dict[str, object]] = []
        for assistant_request in assistant_requests:
            assistant_request.raw_payload["headers"] = {
                "x_webhook_event": x_webhook_event,
                "x_idempotency_key": x_idempotency_key,
            }
            assistant_response = engine.handle_message(assistant_request)
            outbound_deliveries.append(
                kapso_adapter.send_response(assistant_request, assistant_response)
            )
            replies.append(assistant_response.reply)

        return {
            "processed": len(assistant_requests),
            "event": x_webhook_event,
            "replies": replies,
            "outbound_deliveries": outbound_deliveries,
        }

    @api.get("/procurement-ui", include_in_schema=False)
    def procurement_ui() -> RedirectResponse:
        return RedirectResponse(url="/ui/index.html")

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        api.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return api


app = create_app()

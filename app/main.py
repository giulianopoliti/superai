import asyncio
import json
import logging
import re
import shutil
import tempfile
import unicodedata
from contextlib import asynccontextmanager, suppress
from datetime import date
from pathlib import Path
from time import monotonic
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.assistant.agent.executor import AgentExecutionService
from app.assistant.engine import AssistantEngine
from app.assistant.intent_router import IntentRouter
from app.assistant.llm_intent_router import LLMIntentRouter
from app.assistant.tools.catalog import CatalogResolveFamiliesTool, CatalogResolveProductsTool
from app.assistant.tools.filters import UnresolvedSalesFilter
from app.assistant.tools.queries import CatalogQueryTool, SalesQueryTool
from app.assistant.tools.registry import AssistantToolRegistry
from app.assistant.tools.sales import SalesTopProductsTool
from app.assistant.tools.schemas import (
    AssistantToolName,
    ToolExecutionContext,
    TopProductsQuery,
    TopProductsToolResponse,
)
from app.channels.whatsapp_kapso import (
    KapsoWebhookError,
    KapsoWhatsAppAdapter,
    verify_kapso_signature,
)
from app.db.models import SupplierModel
from app.db.repositories.conversations import InMemoryConversationMessageRepository
from app.db.repositories.reminders import InMemoryReminderRepository
from app.db.repositories.sql_conversations import SqlConversationMessageRepository
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_reminders import SqlReminderRepository
from app.db.repositories.sql_sales import SqlSalesRepository
from app.db.session import SessionLocal
from app.modules.catalog.resolver import CatalogResolverService
from app.modules.operations.schemas import DailySummaryResponse, OperationsDashboardResponse
from app.modules.operations.service import OperationsDashboardService
from app.modules.procurement.analysis_contracts import (
    AnalysisMethod,
    AnalysisMethodCapability,
    AnalysisMethodsResponse,
    AnalysisSummary,
    DocumentAnalysisDetail,
    PendingBlock,
)
from app.modules.procurement.analysis_reporting import summary_from_metadata
from app.modules.procurement.catalog_import import PosCatalogImportService
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.purchase_plan import SupplierOfferPurchasePlanService
from app.modules.procurement.purchase_plan_pdf import build_supplier_offer_purchase_plan_pdf
from app.modules.procurement.review import ProductMatchReviewService
from app.modules.procurement.schemas import (
    CatalogImportPathRequest,
    CatalogImportResult,
    CatalogSyncStatus,
    Product,
    ProductMatchCorrectionRequest,
    ProductMatchFeedback,
    ProductMatchReviewList,
    ProductMatchReviewRequest,
    SupplierOfferCompareRequest,
    SupplierOfferCompareResponse,
    SupplierOfferDocument,
    SupplierOfferDocumentAnalysisResponse,
    SupplierOfferDocumentSummary,
    SupplierOfferImportResult,
    SupplierOfferJsonPathRequest,
    SupplierOfferPurchasePlan,
    SupplierPriceComparisonRequest,
    SupplierPriceComparisonResponse,
    SupplierPriceUpsertRequest,
    SupplierPriceUpsertResponse,
)
from app.modules.procurement.supplier_offers import SupplierOfferService
from app.modules.procurement.supplier_price_comparison import SupplierPriceComparisonService
from app.modules.reminders.dispatcher import ReminderDispatcher
from app.modules.reminders.service import ReminderService
from app.modules.sales.purchase_suggestions import PurchaseSuggestionService
from app.modules.sales.sales_import import PosSalesImportService
from app.modules.sales.schemas import (
    ProductSalesSummary,
    PurchaseSuggestionsResponse,
    SalesHistoryStatus,
    SalesImportResult,
    SalesTopProductsResponse,
)
from app.providers.documents.dual import ChunkedDocumentExtractionProvider
from app.providers.documents.fallback import (
    DocumentExtractionUnavailableError,
)
from app.providers.documents.local_text import LocalTextSupplierOfferProvider
from app.providers.documents.openai import OpenAISupplierOfferDocumentProvider
from app.providers.documents.pack_review import OpenAIPackReviewer, review_extraction_packs
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai import OpenAILLMProvider
from app.providers.notifications.kapso import KapsoNotificationProvider
from app.schemas.assistant import AssistantRequest, AssistantResponse
from app.settings import settings

logger = logging.getLogger("uvicorn.error")


def build_document_analysis_service():
    """Compose request-scoped A/B components without opening DB sessions or calling APIs."""
    if not settings.document_analysis_v2_enabled:
        return None
    # Imports are deferred so an unfinished optional component cannot break legacy startup.
    try:
        from app.modules.procurement.block_interpretation import (
            DeterministicBlockAssembler,
            detect_value_candidates,
        )
        from app.modules.procurement.decision_policy import DocumentDecisionPolicy
        from app.modules.procurement.document_analysis import (
            MethodBlockAssembler,
            SupplierOfferDocumentAnalysisService,
        )
        from app.providers.jev_analysis import (
            JevAnalysisClient,
            JevAnalysisProvider,
            build_classification_questions,
        )
    except ImportError:
        return None
    if not settings.openai_api_key:
        return None
    if not callable(getattr(ProductMatchService, "retrieve_analysis_candidates", None)):
        return None
    reader = OpenAISupplierOfferDocumentProvider(
        api_key=settings.openai_api_key, model=settings.openai_model,
        timeout_seconds=settings.document_extraction_timeout_seconds,
    )
    jev = None
    if settings.typesafe_api_key:
        try:
            jev = JevAnalysisProvider(client=JevAnalysisClient(
                api_key=settings.typesafe_api_key, model=settings.jev_reconciliation_model,
                timeout_seconds=settings.document_extraction_timeout_seconds,
                max_calls=settings.document_analysis_max_calls,
            ))
        except (ValueError, OSError):
            return None
    repository = None
    supplier_ids = {}

    class CatalogSnapshot:
        def __init__(self, inner):
            self.inner = inner
            self.products = {}
            self.new_supplier_ids = set()

        def list_products(self, *, business_id):
            if business_id not in self.products:
                self.products[business_id] = self.inner.list_products(business_id=business_id)
            return self.products[business_id]

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def list_product_match_feedback_rules(self, *, business_id, supplier_id, normalized_names):
            if supplier_id in self.new_supplier_ids:
                return []
            return self.inner.list_product_match_feedback_rules(
                business_id=business_id, supplier_id=supplier_id, normalized_names=normalized_names,
            )

    def retrieve(*, business_id, offers, supplier_name, internal_top_k):
        nonlocal repository
        # Lazy construction: GET capabilities never opens DB sessions or performs writes.
        if repository is None:
            repository = CatalogSnapshot(build_procurement_repository())
        key = (business_id, supplier_name)
        if key not in supplier_ids:
            if not supplier_name:
                return {offer.offer_id: [] for offer in offers}
            from app.modules.procurement.normalization import normalize_text
            # Read-only lookup. New supplier rows are created only by final persistence.
            with SessionLocal() as session:
                supplier_id = session.scalar(select(SupplierModel.id).where(
                    SupplierModel.business_id == business_id,
                    SupplierModel.normalized_name == normalize_text(supplier_name),
                ))
            supplier_ids[key] = supplier_id or uuid4().hex
            if supplier_id is None:
                repository.new_supplier_ids.add(supplier_ids[key])
        conflicts = {}
        found = ProductMatchService(repository).retrieve_analysis_candidates(
            business_id=business_id, supplier_id=supplier_ids[key], offers=offers,
            internal_top_k=internal_top_k, feedback_conflicts=conflicts,
        )
        for offer in offers:
            offer.reason_codes.extend(conflicts.get(offer.offer_id, []))
        return found

    return SupplierOfferDocumentAnalysisService(
        reader=reader, assembler=MethodBlockAssembler(DeterministicBlockAssembler()),
        classifier=jev, decision_provider=jev, policy=DocumentDecisionPolicy(),
        candidate_retriever=retrieve, value_detector=detect_value_candidates,
        question_builder=build_classification_questions,
        max_calls=settings.document_analysis_max_calls,
        timeout_seconds=settings.document_analysis_timeout_seconds,
        max_metadata_bytes=settings.document_analysis_max_metadata_bytes,
        require_decisions=True,
        chunk_workers=settings.document_extraction_chunk_workers,
    )


def analysis_methods_capability():
    service = build_document_analysis_service()
    methods = []
    for method in AnalysisMethod:
        code, reason = None, None
        if not settings.openai_api_key:
            code, reason = "openai_not_configured", "OpenAI no está configurado."
        elif method == AnalysisMethod.OPENAI_BLOCKS_JEV and not settings.typesafe_api_key:
            code, reason = "jev_not_configured", "JEV no está configurado."
        elif method == AnalysisMethod.OPENAI_BLOCKS_JEV and (
            service is None or not service.supports(method)
        ):
            code = "method_disabled" if not settings.document_analysis_v2_enabled \
                else "method_not_integrated"
            reason = "Este método todavía no está habilitado."
        methods.append(AnalysisMethodCapability(
            id=method, available=code is None,
            unavailable_reason_code=code, unavailable_reason=reason,
        ))
    return AnalysisMethodsResponse(methods=methods)


def _log_catalog_analysis_stage(
    request_id: str,
    stage: str,
    started_at: float,
    **details: object,
) -> float:
    now = monotonic()
    detail_text = " ".join(f"{key}={value}" for key, value in details.items())
    logger.info(
        "catalog_analysis request_id=%s stage=%s duration_ms=%.1f%s",
        request_id,
        stage,
        (now - started_at) * 1000,
        f" {detail_text}" if detail_text else "",
    )
    return now


def build_repositories():
    if SessionLocal is not None:
        return SqlReminderRepository(SessionLocal), SqlConversationMessageRepository(SessionLocal)
    return InMemoryReminderRepository(), InMemoryConversationMessageRepository()


def build_procurement_repository() -> SqlProcurementRepository:
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")
    return SqlProcurementRepository(SessionLocal)


def build_sales_repository() -> SqlSalesRepository:
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")
    return SqlSalesRepository(SessionLocal)


def build_operations_service() -> OperationsDashboardService:
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")
    return OperationsDashboardService(
        sales_repository=SqlSalesRepository(SessionLocal),
        procurement_repository=SqlProcurementRepository(SessionLocal),
        reminder_repository=SqlReminderRepository(SessionLocal),
    )


def build_tool_registry() -> AssistantToolRegistry:
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")
    procurement_repository = SqlProcurementRepository(SessionLocal)
    catalog_resolver = CatalogResolverService(procurement_repository)
    return AssistantToolRegistry(
        tools=[
            *[
                SalesQueryTool(name, SqlSalesRepository(SessionLocal), catalog_resolver)
                for name in (
                    AssistantToolName.SALES_SUMMARY,
                    AssistantToolName.SALES_PRODUCT_HISTORY,
                    AssistantToolName.SALES_COMPARE_PERIODS,
                )
            ],
            *[
                CatalogQueryTool(name, procurement_repository, catalog_resolver)
                for name in (
                    AssistantToolName.CATALOG_SEARCH_PRODUCTS,
                    AssistantToolName.INVENTORY_STOCK_STATUS,
                )
            ],
            CatalogResolveFamiliesTool(catalog_resolver),
            CatalogResolveProductsTool(catalog_resolver),
            SalesTopProductsTool(
                SqlSalesRepository(SessionLocal),
                catalog_resolver,
            ),
        ]
    )


def build_llm_provider() -> LLMProvider | None:
    if not settings.llm_enabled:
        return None

    # Honor old Gemini settings without making calls to the suspended provider.
    provider = settings.llm_provider.strip().lower()
    if provider in {"openai", "gemini"} and settings.openai_api_key:
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
        raise HTTPException(status_code=400, detail="Gemini está suspendido. Usá OpenAI.")
    if provider in {"auto", "openai"}:
        if not settings.openai_api_key:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")
        reader = OpenAISupplierOfferDocumentProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.document_extraction_timeout_seconds,
        )
        return ChunkedDocumentExtractionProvider(
            [("openai", reader)],
            # Existing .env files may still request six-page blocks.
            chunk_size=min(settings.document_extraction_chunk_size, 2),
            chunk_workers=settings.document_extraction_chunk_workers,
            retry_workers=settings.document_extraction_retry_workers,
            timeout_seconds=settings.document_extraction_timeout_seconds,
            retry_low_coverage=settings.document_extraction_retry_low_coverage,
        )
    raise HTTPException(status_code=400, detail=f"Unsupported extraction provider: {provider_name}")


def safe_upload_filename(filename: str) -> str:
    path = Path(filename)
    stem = unicodedata.normalize("NFKD", path.stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    suffix = unicodedata.normalize("NFKD", path.suffix).encode("ascii", "ignore").decode("ascii")
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix)
    return f"{stem or 'upload'}{suffix or path.suffix or '.bin'}"


def build_engine(
    reminder_repository=None,
    conversation_repository=None,
    operations_service: OperationsDashboardService | None = None,
    tool_registry: AssistantToolRegistry | None = None,
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
    agent_executor = (
        AgentExecutionService(
            tool_registry=tool_registry,
            max_steps=settings.agent_max_steps,
        )
        if settings.agent_enabled and tool_registry is not None
        else None
    )
    return AssistantEngine(
        intent_router=intent_router,
        reminder_service=reminder_service,
        conversation_repository=conversation_repository,
        operations_service=operations_service,
        tool_registry=tool_registry,
        agent_planner=llm_provider if settings.agent_enabled else None,
        agent_executor=agent_executor,
        agent_minimum_confidence=settings.agent_minimum_confidence,
    )


def create_app() -> FastAPI:
    reminder_repository, conversation_repository = build_repositories()
    reminder_service = ReminderService(reminder_repository)
    operations_service = build_operations_service() if SessionLocal is not None else None
    tool_registry = build_tool_registry() if SessionLocal is not None else None
    engine = build_engine(
        reminder_repository,
        conversation_repository,
        operations_service,
        tool_registry,
    )
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

    @api.exception_handler(OperationalError)
    def database_connection_error(_: Request, exc: OperationalError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "No se pudo conectar a la base de datos. Revisa DATABASE_URL, "
                    "la conexion a internet/VPN y el estado de Supabase."
                ),
                "error": str(exc.orig) if getattr(exc, "orig", None) is not None else str(exc),
            },
        )

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/operations/dashboard", response_model=OperationsDashboardResponse)
    def operations_dashboard(
        business_id: str = settings.default_business_id,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 10,
        target_coverage_days: int = 7,
        safety_days: int = 0,
        use_stock: bool = True,
    ) -> OperationsDashboardResponse:
        if target_coverage_days < 1 or target_coverage_days > 120:
            raise HTTPException(
                status_code=400,
                detail="target_coverage_days must be between 1 and 120.",
            )
        if safety_days < 0 or safety_days > 60:
            raise HTTPException(status_code=400, detail="safety_days must be between 0 and 60.")
        return build_operations_service().build_dashboard(
            business_id=business_id,
            date_from=date.fromisoformat(date_from) if date_from else None,
            date_to=date.fromisoformat(date_to) if date_to else None,
            limit=limit,
            target_coverage_days=target_coverage_days,
            safety_days=safety_days,
            use_stock=use_stock,
        )

    @api.get("/operations/alerts")
    def operations_alerts(
        business_id: str = settings.default_business_id,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 10,
    ) -> dict[str, object]:
        dashboard = build_operations_service().build_dashboard(
            business_id=business_id,
            date_from=date.fromisoformat(date_from) if date_from else None,
            date_to=date.fromisoformat(date_to) if date_to else None,
            limit=limit,
        )
        return {
            "business_id": dashboard.business_id,
            "generated_at": dashboard.generated_at,
            "date_from": dashboard.date_from,
            "date_to": dashboard.date_to,
            "alerts": dashboard.alerts,
        }

    @api.get("/operations/daily-summary", response_model=DailySummaryResponse)
    def operations_daily_summary(
        business_id: str = settings.default_business_id,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> DailySummaryResponse:
        return build_operations_service().build_daily_summary(
            business_id=business_id,
            date_from=date.fromisoformat(date_from) if date_from else None,
            date_to=date.fromisoformat(date_to) if date_to else None,
        )

    @api.post("/sales/imports/from-file", response_model=SalesImportResult)
    async def import_sales_from_file(
        file: Annotated[UploadFile, File()],
        business_id: Annotated[str, Form()] = settings.default_business_id,
    ) -> SalesImportResult:
        if not file.filename:
            raise HTTPException(status_code=400, detail="filename is required.")

        service = PosSalesImportService(build_sales_repository())
        with tempfile.TemporaryDirectory(prefix="stock-ai-sales-") as temp_dir:
            csv_path = Path(temp_dir) / Path(file.filename).name
            with csv_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            return service.import_csv(business_id=business_id, csv_path=csv_path)

    @api.get("/sales/status", response_model=SalesHistoryStatus)
    def sales_history_status(
        business_id: str = settings.default_business_id,
    ) -> SalesHistoryStatus:
        return build_sales_repository().get_sales_history_status(business_id=business_id)

    @api.post(
        "/assistant/tools/sales/top-products",
        response_model=TopProductsToolResponse,
    )
    def assistant_tool_sales_top_products(
        request: TopProductsQuery,
    ) -> TopProductsToolResponse:
        if not request.business_id:
            raise HTTPException(status_code=400, detail="business_id is required.")
        tool = SalesTopProductsTool(
            build_sales_repository(),
            CatalogResolverService(build_procurement_repository()),
        )
        try:
            return tool.run_query(
                request, context=ToolExecutionContext(business_id=request.business_id)
            )
        except UnresolvedSalesFilter as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.get("/sales/products/{product_id}/summary", response_model=ProductSalesSummary)
    def product_sales_summary(
        product_id: str,
        date_from: str,
        date_to: str,
        business_id: str = settings.default_business_id,
    ) -> ProductSalesSummary:
        repository = build_sales_repository()
        summary = repository.summarize_product_sales(
            business_id=business_id,
            product_id=product_id,
            date_from=date.fromisoformat(date_from),
            date_to=date.fromisoformat(date_to),
        )
        if summary is None:
            raise HTTPException(status_code=404, detail="Product not found.")
        return summary

    @api.get("/sales/top-products", response_model=SalesTopProductsResponse)
    def sales_top_products(
        date_from: str,
        date_to: str,
        business_id: str = settings.default_business_id,
        sort: str = "quantity",
        limit: int = 20,
    ) -> SalesTopProductsResponse:
        if sort not in {"quantity", "revenue", "margin"}:
            raise HTTPException(
                status_code=400,
                detail="sort must be quantity, revenue, or margin.",
            )
        parsed_date_from = date.fromisoformat(date_from)
        parsed_date_to = date.fromisoformat(date_to)
        products = build_sales_repository().top_products(
            business_id=business_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            sort=sort,
            limit=min(max(limit, 1), 100),
        )
        return SalesTopProductsResponse(
            business_id=business_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            sort=sort,
            products=products,
        )

    @api.get("/sales/purchase-suggestions", response_model=PurchaseSuggestionsResponse)
    def sales_purchase_suggestions(
        date_from: str,
        date_to: str,
        business_id: str = settings.default_business_id,
        sort: str = "quantity",
        limit: int = 20,
        target_coverage_days: int = 7,
        safety_days: int = 0,
        use_stock: bool = False,
        ignore_negative_stock: bool = True,
    ) -> PurchaseSuggestionsResponse:
        if sort not in {"quantity", "revenue", "margin"}:
            raise HTTPException(
                status_code=400,
                detail="sort must be quantity, revenue, or margin.",
            )
        if target_coverage_days < 1 or target_coverage_days > 120:
            raise HTTPException(
                status_code=400,
                detail="target_coverage_days must be between 1 and 120.",
            )
        if safety_days < 0 or safety_days > 60:
            raise HTTPException(status_code=400, detail="safety_days must be between 0 and 60.")

        parsed_date_from = date.fromisoformat(date_from)
        parsed_date_to = date.fromisoformat(date_to)
        products = build_sales_repository().top_products(
            business_id=business_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            sort=sort,
            limit=min(max(limit, 1), 100),
        )
        suggestions = PurchaseSuggestionService().build_suggestions(
            sales_summaries=products,
            target_coverage_days=target_coverage_days,
            safety_days=safety_days,
            use_stock=use_stock,
            ignore_negative_stock=ignore_negative_stock,
        )
        return PurchaseSuggestionsResponse(
            business_id=business_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            sort=sort,
            target_coverage_days=target_coverage_days,
            safety_days=safety_days,
            use_stock=use_stock,
            ignore_negative_stock=ignore_negative_stock,
            suggestions=suggestions,
        )

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

    @api.get("/procurement/catalog-imports/status", response_model=CatalogSyncStatus)
    def catalog_sync_status(
        business_id: str = settings.default_business_id,
    ) -> CatalogSyncStatus:
        return build_procurement_repository().get_catalog_sync_status(business_id=business_id)

    @api.get("/procurement/products/search", response_model=list[Product])
    def search_products(
        q: str,
        business_id: str = settings.default_business_id,
        limit: int = 20,
    ) -> list[Product]:
        return build_procurement_repository().search_products(
            business_id=business_id,
            query=q,
            limit=min(max(limit, 1), 50),
        )

    @api.post(
        "/procurement/supplier-prices",
        response_model=SupplierPriceUpsertResponse,
    )
    def record_supplier_prices(
        request: SupplierPriceUpsertRequest,
    ) -> SupplierPriceUpsertResponse:
        service = SupplierPriceComparisonService(build_procurement_repository())
        try:
            return service.record_supplier_prices(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post(
        "/procurement/supplier-price-comparisons",
        response_model=SupplierPriceComparisonResponse,
    )
    def compare_supplier_prices(
        request: SupplierPriceComparisonRequest,
    ) -> SupplierPriceComparisonResponse:
        service = SupplierPriceComparisonService(build_procurement_repository())
        try:
            return service.compare_supplier_prices(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/procurement/catalog-imports/from-file", response_model=CatalogImportResult)
    async def import_catalog_from_file(
        file: Annotated[UploadFile, File()],
        business_id: Annotated[str, Form()] = settings.default_business_id,
        families_file: Annotated[UploadFile | None, File()] = None,
    ) -> CatalogImportResult:
        if not file.filename:
            raise HTTPException(status_code=400, detail="filename is required.")

        service = PosCatalogImportService(build_procurement_repository())
        with tempfile.TemporaryDirectory(prefix="stock-ai-catalog-") as temp_dir:
            csv_path = Path(temp_dir) / Path(file.filename).name
            with csv_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            families_path = None
            if families_file is not None and families_file.filename:
                families_path = Path(temp_dir) / "familias.csv"
                with families_path.open("wb") as destination:
                    shutil.copyfileobj(families_file.file, destination)
            return service.import_csv(
                business_id=business_id,
                csv_path=csv_path,
                families_csv_path=families_path,
            )

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

    @api.get(
        "/procurement/supplier-offers/summaries",
        response_model=list[SupplierOfferDocumentSummary],
    )
    def list_supplier_offer_document_summaries(
        business_id: str = settings.default_business_id,
        limit: int = 20,
    ) -> list[SupplierOfferDocumentSummary]:
        repository = build_procurement_repository()
        summaries = repository.list_supplier_offer_document_summaries(
            business_id=business_id,
            limit=min(max(limit, 1), 100),
        )
        for entry in summaries:
            summary = summary_from_metadata(entry.document.metadata)
            if summary is not None:
                entry.document = entry.document.model_copy(update={
                    "metadata": {**entry.document.metadata,
                                 "analysis": summary.model_dump(mode="json")},
                })
        return summaries

    @api.get(
        "/procurement/supplier-offers/analysis-methods",
        response_model=AnalysisMethodsResponse,
    )
    def get_supplier_offer_analysis_methods() -> AnalysisMethodsResponse:
        return analysis_methods_capability()

    @api.get(
        "/procurement/supplier-offers/{document_id}/analysis",
        response_model=DocumentAnalysisDetail,
    )
    def get_supplier_offer_analysis(
        document_id: str, business_id: str = settings.default_business_id,
    ) -> DocumentAnalysisDetail:
        document = build_procurement_repository().get_supplier_offer_document(
            business_id=business_id, supplier_offer_document_id=document_id,
        )
        if document is None or document.business_id != business_id:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        ledger = document.metadata.get("analysis_ledger", {})
        return DocumentAnalysisDetail(
            document_id=document.id,
            analysis=summary_from_metadata(document.metadata),
            pending_blocks=[PendingBlock.model_validate(b)
                            for b in ledger.get("pending_blocks", [])],
        )

    @api.post(
        "/procurement/supplier-offers/from-document",
        response_model=SupplierOfferDocumentAnalysisResponse,
    )
    def import_supplier_offer_from_document(
        file: Annotated[UploadFile, File()],
        business_id: Annotated[str, Form()] = settings.default_business_id,
        supplier_name: Annotated[str | None, Form()] = None,
        extraction_provider: Annotated[str, Form()] = "auto",
        max_candidates: Annotated[int, Form()] = 1,
        persist_candidates: Annotated[bool, Form()] = True,
        analysis_method: Annotated[str, Form()] = "openai_structured",
        identity_probability: Annotated[float, Form(ge=0, le=1)] = 0.85,
        price_probability: Annotated[float, Form(ge=0, le=1)] = 0.85,
        decision_confidence: Annotated[float, Form(ge=0, le=1)] = 0.85,
        decision_margin: Annotated[float, Form(ge=0, le=1)] = 0.10,
    ) -> SupplierOfferDocumentAnalysisResponse:
        if not file.filename:
            raise HTTPException(status_code=400, detail="filename is required.")
        if not business_id.strip():
            raise HTTPException(status_code=422, detail="Falta indicar el negocio.")
        try:
            method = AnalysisMethod(analysis_method)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Método de análisis desconocido.") from exc
        analysis_service = build_document_analysis_service()
        if method == AnalysisMethod.OPENAI_BLOCKS_JEV:
            capability = next(c for c in analysis_methods_capability().methods if c.id == method)
            if not capability.available:
                raise HTTPException(status_code=503, detail=capability.unavailable_reason)
            if extraction_provider not in {"auto", "openai"}:
                raise HTTPException(status_code=422,
                                    detail="Este método requiere lectura con OpenAI.")

        request_id = uuid4().hex[:12]
        request_started_at = monotonic()
        stage_started_at = request_started_at
        logger.info(
            "catalog_analysis request_id=%s stage=started provider=%s persist_candidates=%s",
            request_id,
            extraction_provider,
            persist_candidates,
        )
        use_v2 = (extraction_provider in {"auto", "openai"} and analysis_service is not None
                  and analysis_service.supports(method))
        provider = None if use_v2 else build_supplier_offer_document_provider(extraction_provider)
        repository = build_procurement_repository()
        supplier_offer_service = SupplierOfferService(
            repository, max_metadata_bytes=settings.document_analysis_max_metadata_bytes,
        )
        match_service = ProductMatchService(repository)
        original_filename = Path(file.filename).name

        with tempfile.TemporaryDirectory(prefix="stock-ai-offer-") as temp_dir:
            document_path = Path(temp_dir) / safe_upload_filename(original_filename)
            with document_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            stage_started_at = _log_catalog_analysis_stage(
                request_id,
                "upload_saved",
                stage_started_at,
                file_size_bytes=document_path.stat().st_size,
                file_type=document_path.suffix.lower() or "unknown",
            )

            try:
                if use_v2:
                    from app.modules.procurement.decision_policy import DecisionThresholds

                    extraction = analysis_service.analyze(
                        business_id=business_id, document_path=document_path,
                        method=method, supplier_hint=supplier_name,
                        decision_thresholds=DecisionThresholds(
                            identity_probability=identity_probability,
                            price_probability=price_probability,
                            confidence=decision_confidence,
                            margin=decision_margin,
                        ),
                    )
                else:
                    extraction = provider.extract_supplier_offer(
                        document_path=document_path,
                        supplier_hint=supplier_name,
                    )
                stage_started_at = _log_catalog_analysis_stage(
                    request_id,
                    "document_extracted",
                    stage_started_at,
                    item_count=len(extraction.items),
                    extraction_id=extraction.document_metadata.get("extraction_id", "none"),
                    provider_calls=(len(extraction.document_metadata["analysis_ledger"]["calls"])
                                    if use_v2 else extraction.document_metadata.get(
                                        "initial_provider_calls", "unknown")),
                    retry_calls=(sum(c.get("phase") == "reread" for c in
                                     extraction.document_metadata["analysis_ledger"]["calls"])
                                 if use_v2 else extraction.document_metadata.get(
                                     "retry_provider_calls", "unknown")),
                )
                if extraction_provider != "local_text" and not use_v2:
                    reviewer = (
                        OpenAIPackReviewer(
                            api_key=settings.openai_api_key,
                            model=settings.openai_model,
                            timeout_seconds=settings.document_extraction_timeout_seconds,
                        )
                        if settings.openai_api_key
                        else None
                    )
                    review_extraction_packs(
                        extraction,
                        document_path=document_path,
                        reviewer=reviewer,
                    )
                    stage_started_at = _log_catalog_analysis_stage(
                        request_id,
                        "pack_review_completed",
                        stage_started_at,
                        reviewed_count=extraction.document_metadata.get("pack_review_count", 0),
                        pending_count=extraction.document_metadata.get(
                            "pack_needs_review_count", 0
                        ),
                    )
            except DocumentExtractionUnavailableError as exc:
                logger.warning(
                    "catalog_analysis request_id=%s stage=document_extraction_failed "
                    "error_type=%s total_duration_ms=%.1f",
                    request_id,
                    type(exc).__name__,
                    (monotonic() - request_started_at) * 1000,
                )
                raise HTTPException(
                    status_code=503,
                    detail=str(exc),
                ) from exc
            except Exception as exc:
                logger.exception(
                    "catalog_analysis request_id=%s stage=document_processing_failed "
                    "error_type=%s total_duration_ms=%.1f",
                    request_id,
                    type(exc).__name__,
                    (monotonic() - request_started_at) * 1000,
                )
                raise HTTPException(
                    status_code=422,
                    detail=f"Document extraction failed: {exc}",
                ) from exc

        extraction.source_filename = original_filename
        resolved_supplier_name = extraction.supplier_name or supplier_name
        if not resolved_supplier_name:
            raise HTTPException(
                status_code=422,
                detail="Supplier name could not be extracted. Send supplier_name.",
            )
        has_evidence = bool(extraction.document_metadata.get("analysis_ledger", {}).get("blocks")
                            or extraction.document_metadata.get("analysis_ledger", {}).get(
                                "invalid_evidence"))
        if not extraction.items and not extraction.document_metadata.get("needs_review_pages") \
                and not has_evidence:
            raise HTTPException(status_code=422, detail="No supplier offer items were extracted.")

        if not use_v2 and extraction_provider in {"auto", "openai"}:
            # Record the real OpenAI path, without pretending legacy exposes V2 blocks/usage.
            summary = AnalysisSummary(
                run_id=request_id, method=method,
                status="partial" if extraction.document_metadata.get("needs_review_pages")
                else "completed",
                page_count=extraction.document_metadata.get("page_count", 1),
                observed_block_count=0, interpreted_offer_count=len(extraction.items),
                pending_block_count=0,
                price_review_offer_count=sum(i.price_type.value == "unknown"
                                             for i in extraction.items),
                review_page_numbers=extraction.document_metadata.get("needs_review_pages", []),
                duration_seconds=monotonic()-request_started_at,
            )
            extraction.document_metadata["analysis"] = summary.model_dump(mode="json")
            extraction.document_metadata["analysis_path"] = "legacy_openai"

        import_result = supplier_offer_service.create_offer_from_extraction(
            business_id=business_id,
            supplier_name=resolved_supplier_name,
            extraction=extraction,
        )
        stage_started_at = _log_catalog_analysis_stage(
            request_id,
            "offer_persisted",
            stage_started_at,
            item_count=len(import_result.items),
        )
        report = match_service.compare_supplier_offer(
            business_id=business_id,
            supplier_offer_document_id=import_result.document.id,
            max_candidates_per_item=max_candidates,
            trace_id=request_id,
        )
        stage_started_at = _log_catalog_analysis_stage(
            request_id,
            "catalog_matched",
            stage_started_at,
            candidate_count=len(report.candidates),
        )
        persisted_count = 0
        if persist_candidates:
            persisted_count = len(match_service.save_supplier_offer_candidates(report=report))
        stage_started_at = _log_catalog_analysis_stage(
            request_id,
            "candidates_persisted",
            stage_started_at,
            persisted_count=persisted_count,
        )
        _log_catalog_analysis_stage(
            request_id,
            "completed",
            request_started_at,
            persisted_count=persisted_count,
            total_items=len(import_result.items),
        )
        stored_ledger = import_result.document.metadata.get("analysis_ledger", {})
        return SupplierOfferDocumentAnalysisResponse(
            import_result=import_result,
            comparison=SupplierOfferCompareResponse(
                report=report,
                persisted_count=persisted_count,
            ),
            extraction_warnings=extraction.warnings,
            analysis=summary_from_metadata(import_result.document.metadata),
            pending_blocks=[PendingBlock.model_validate(b)
                            for b in stored_ledger.get("pending_blocks", [])],
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

    @api.get(
        "/procurement/supplier-offers/{supplier_offer_document_id}/purchase-plan",
        response_model=SupplierOfferPurchasePlan,
    )
    def supplier_offer_purchase_plan(
        supplier_offer_document_id: str,
        date_from: str,
        date_to: str,
        business_id: str = settings.default_business_id,
        target_coverage_days: int | None = None,
        use_stock: bool = False,
        ignore_negative_stock: bool = True,
        sort_by: str = "sales_then_savings",
        only_positive_savings: bool = False,
    ) -> SupplierOfferPurchasePlan:
        if sort_by not in {
            "sales_then_savings",
            "savings_then_sales",
            "estimated_savings",
            "supplier_order",
        }:
            raise HTTPException(
                status_code=400,
                detail=(
                    "sort_by must be sales_then_savings, savings_then_sales, "
                    "estimated_savings, or supplier_order."
                ),
            )
        if target_coverage_days is not None and (
            target_coverage_days < 1 or target_coverage_days > 120
        ):
            raise HTTPException(
                status_code=400,
                detail="target_coverage_days must be between 1 and 120.",
            )
        service = SupplierOfferPurchasePlanService(
            review_service=ProductMatchReviewService(build_procurement_repository()),
            sales_repository=build_sales_repository(),
        )
        return service.build_plan(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            date_from=date.fromisoformat(date_from),
            date_to=date.fromisoformat(date_to),
            target_coverage_days=target_coverage_days,
            use_stock=use_stock,
            ignore_negative_stock=ignore_negative_stock,
            sort_by=sort_by,
            only_positive_savings=only_positive_savings,
        )

    @api.get(
        "/procurement/supplier-offers/{supplier_offer_document_id}/purchase-plan.pdf",
        response_class=Response,
    )
    def supplier_offer_purchase_plan_pdf(
        supplier_offer_document_id: str,
        date_from: str,
        date_to: str,
        business_id: str = settings.default_business_id,
        target_coverage_days: int | None = None,
        use_stock: bool = False,
        ignore_negative_stock: bool = True,
        sort_by: str = "sales_then_savings",
        only_positive_savings: bool = False,
    ) -> Response:
        plan = supplier_offer_purchase_plan(
            supplier_offer_document_id=supplier_offer_document_id,
            date_from=date_from,
            date_to=date_to,
            business_id=business_id,
            target_coverage_days=target_coverage_days,
            use_stock=use_stock,
            ignore_negative_stock=ignore_negative_stock,
            sort_by=sort_by,
            only_positive_savings=only_positive_savings,
        )
        pdf = build_supplier_offer_purchase_plan_pdf(plan)
        filename = f"plan-compra-{supplier_offer_document_id}.pdf"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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

    @api.get("/ui/index.html", include_in_schema=False)
    def procurement_ui_index() -> FileResponse:
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    if static_dir.exists():
        api.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return api


app = create_app()

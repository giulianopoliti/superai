import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.assistant.agent.executor import AgentExecutionService
from app.assistant.agent.schemas import AgentPlanMode
from app.assistant.intent_router import IGNORE_STOCK, IntentRouter, asks_for_sales_growth
from app.assistant.sales_context import planner_context, sales_follow_up, sales_scope
from app.assistant.tools.formatting import format_number
from app.assistant.tools.registry import AssistantToolRegistry
from app.assistant.tools.schemas import AssistantToolName, ToolExecutionContext
from app.db.repositories.conversations import ConversationMessageRepository
from app.modules.operations.service import OperationsDashboardService
from app.modules.procurement.normalization import normalize_text
from app.modules.reminders.service import ReminderService
from app.providers.llm.planner import LLMPlannerProvider
from app.schemas.assistant import AssistantAction, AssistantRequest, AssistantResponse, Channel
from app.schemas.intents import IntentName

logger = logging.getLogger(__name__)

ASSISTANT_TIMEZONE = ZoneInfo("America/Buenos_Aires")


class AssistantEngine:
    def __init__(
        self,
        *,
        intent_router: IntentRouter,
        reminder_service: ReminderService,
        conversation_repository: ConversationMessageRepository,
        operations_service: OperationsDashboardService | None = None,
        tool_registry: AssistantToolRegistry | None = None,
        agent_planner: LLMPlannerProvider | None = None,
        agent_executor: AgentExecutionService | None = None,
        agent_minimum_confidence: float = 0.7,
    ) -> None:
        self._intent_router = intent_router
        self._reminder_service = reminder_service
        self._conversation_repository = conversation_repository
        self._operations_service = operations_service
        self._tool_registry = tool_registry
        self._agent_planner = agent_planner
        self._agent_executor = agent_executor
        self._agent_minimum_confidence = agent_minimum_confidence

    def handle_message(self, request: AssistantRequest) -> AssistantResponse:
        messages = (
            [] if (
                request.channel == Channel.WEB
                and request.external_user_id == "web-user"
                and request.conversation_id is None
            )
            else self._conversation_repository.recent_messages(request)
        )
        context = planner_context(messages)
        self._conversation_repository.save_inbound(request)
        diagnostics = {}
        # Explicit supported requests have deterministic semantics, including dates
        # and reminder writes. A planner must not replace them with a generic reply.
        local_intent = IntentRouter().route(request)
        common_tool = local_intent.entities.get("tool_name")
        if local_intent.intent == IntentName.UNKNOWN or asks_for_sales_growth(
            normalize_text(request.text)
        ) or (
            common_tool == AssistantToolName.SALES_COMPARE_PERIODS
            and context["sales_scope"].get("tool") == AssistantToolName.SALES_TOP_PRODUCTS
        ) or (
            common_tool == AssistantToolName.SALES_BASKET_ASSOCIATIONS
            and context["sales_scope"].get("tool") == AssistantToolName.SALES_BASKET_ASSOCIATIONS
        ):
            follow_up = sales_follow_up(
                request.text or "", request.timestamp, context["sales_scope"]
            )
            if isinstance(follow_up, str):
                response = AssistantResponse(
                    reply=follow_up, metadata={"requires_clarification": True}
                )
                self._conversation_repository.save_outbound(request, response)
                return response
            if follow_up:
                tool, arguments = follow_up
                response = self._execute_sales_top_products(
                    request, arguments, tool_name=tool, intent=IntentName.QUERY_TOOL
                )
                self._conversation_repository.save_outbound(request, response)
                return response
        request._planner_context = context
        agent_response = (
            self._try_agent(request, diagnostics)
            if (
                local_intent.intent == IntentName.UNKNOWN
                and not self._is_greeting_or_help(request.text)
            )
            else None
        )
        if agent_response is not None:
            self._conversation_repository.save_outbound(request, agent_response)
            return agent_response

        intent_result = (
            local_intent
            if self._is_greeting_or_help(request.text)
            or (
                local_intent.intent != IntentName.UNKNOWN
                and local_intent.intent not in {
                    IntentName.CREATE_REMINDER,
                    IntentName.MARK_REMINDER_DONE,
                    IntentName.LIST_REMINDERS,
                }
            )
            else self._intent_router.route(request)
        )

        if intent_result.requires_clarification:
            response = AssistantResponse(
                reply=intent_result.clarification_question
                or "Me falta un dato para guardar eso. Me lo decis?",
                requires_confirmation=True,
                confirmation_payload={"intent": intent_result.intent},
                metadata={
                    "intent": intent_result.model_dump(mode="json"),
                    "requires_clarification": True,
                    **diagnostics,
                },
            )
            self._conversation_repository.save_outbound(request, response)
            return response

        if intent_result.intent == IntentName.CREATE_REMINDER:
            response = self._create_reminder(request, intent_result.entities)
        elif intent_result.intent == IntentName.LIST_REMINDERS:
            response = self._list_reminders(request)
        elif intent_result.intent == IntentName.MARK_REMINDER_DONE:
            response = self._mark_reminder_done(request, intent_result.entities)
        elif intent_result.intent == IntentName.GET_OPERATIONS_DASHBOARD:
            response = self._get_operations_dashboard(request, intent_result.intent)
        elif intent_result.intent == IntentName.LIST_OPERATIONAL_ALERTS:
            response = self._list_operational_alerts(request, intent_result.intent)
        elif intent_result.intent == IntentName.GET_DAILY_SUMMARY:
            response = self._get_daily_summary(request, intent_result.intent)
        elif intent_result.intent == IntentName.QUERY_TOOL:
            response = self._execute_sales_top_products(
                request,
                intent_result.entities.get("arguments", {}),
                tool_name=AssistantToolName(intent_result.entities["tool_name"]),
                intent=IntentName.QUERY_TOOL,
            )
        elif intent_result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS:
            response = self._execute_sales_top_products(request, intent_result.entities)
        else:
            response = AssistantResponse(
                reply=(
                    "Puedo consultar ventas, productos más vendidos, precios y stock del "
                    "catálogo, comparar períodos, sugerir combos y guardar recordatorios. "
                    "Probá con «¿cuánto vendí hoy?» o «stock de Coca»."
                    if self._is_greeting_or_help(request.text)
                    else "No puedo resolver ese pedido con las consultas disponibles. "
                    "Si buscás ventas, indicame qué productos o períodos querés comparar."
                ),
                metadata={"intent": intent_result.model_dump(mode="json")},
            )

        response.metadata.update(diagnostics)
        self._conversation_repository.save_outbound(request, response)
        return response

    def _try_agent(self, request: AssistantRequest, diagnostics: dict) -> AssistantResponse | None:
        if (
            self._agent_planner is None
            or self._agent_executor is None
            or self._tool_registry is None
        ):
            return None
        try:
            plan = self._agent_planner.build_agent_plan(
                request=request,
                tools=self._tool_registry.definitions(),
            )
            if plan.confidence < self._agent_minimum_confidence:
                diagnostics["agent_fallback_reason"] = "low_confidence"
                return None
            if plan.mode == AgentPlanMode.ANSWER_DIRECTLY:
                # Model prose is never a source of business facts. Greetings/help
                # are answered below with the backend's actual capabilities.
                diagnostics["agent_fallback_reason"] = "direct_answer_without_evidence"
                return None
            if IGNORE_STOCK.search(normalize_text(request.text)) and any(
                step.tool in {
                    AssistantToolName.INVENTORY_STOCK_STATUS,
                    AssistantToolName.CATALOG_SEARCH_PRODUCTS,
                }
                for step in plan.steps
            ):
                diagnostics["agent_fallback_reason"] = "inventory_explicitly_excluded"
                return None
            execution = self._agent_executor.execute(plan=plan, request=request)
        except Exception as exc:
            logger.warning("Agent planner/executor failed (%s)", type(exc).__name__)
            diagnostics["agent_fallback_reason"] = "planner_or_execution_error"
            diagnostics["agent_error_type"] = type(exc).__name__
            return None

        if plan.mode == AgentPlanMode.ASK_CLARIFICATION or execution.metadata.get(
            "requires_clarification"
        ):
            return AssistantResponse(
                reply=execution.reply,
                requires_confirmation=True,
                confirmation_payload={"mode": plan.mode},
                metadata={
                    "agent": execution.model_dump(mode="json"),
                    "requires_clarification": True,
                },
            )
        scope = {}
        if execution.steps:
            step = execution.steps[-1]
            scope = sales_scope(step.tool, step.arguments, step.data)
        return AssistantResponse(
            reply=execution.reply,
            actions=[
                AssistantAction(
                    type="agent.step_executed",
                    payload=step.model_dump(mode="json"),
                )
                for step in execution.steps
            ],
            metadata={
                "agent": execution.model_dump(mode="json"),
                **({"sales_scope": scope} if scope else {}),
            },
        )

    def _create_reminder(
        self, request: AssistantRequest, entities: dict[str, object]
    ) -> AssistantResponse:
        title = str(entities.get("title") or "").strip()
        if not title:
            return AssistantResponse(
                reply="Que queres que te recuerde?",
                requires_confirmation=True,
                confirmation_payload={"intent": IntentName.CREATE_REMINDER},
            )

        reminder = self._reminder_service.create_reminder(
            business_id=request.business_id,
            created_by_external_user_id=request.external_user_id,
            title=title,
            due_at=self._parse_due_at(entities.get("due_at")),
        )
        due_text = self._format_due_text(reminder.due_at)
        return AssistantResponse(
            reply=f"Listo, guarde el recordatorio{due_text}: {reminder.title}.",
            actions=[
                AssistantAction(
                    type="reminder.created",
                    payload={
                        "reminder_id": reminder.id,
                        "title": reminder.title,
                        "due_at": reminder.due_at.isoformat() if reminder.due_at else None,
                    },
                )
            ],
            metadata={"intent": IntentName.CREATE_REMINDER},
        )

    def _list_reminders(self, request: AssistantRequest) -> AssistantResponse:
        reminders = self._reminder_service.list_pending_reminders(request.business_id)
        if not reminders:
            return AssistantResponse(
                reply="No tenes recordatorios pendientes.",
                metadata={"intent": IntentName.LIST_REMINDERS, "count": 0},
            )

        reminder_lines = [
            f"- {reminder.title}{self._format_due_text(reminder.due_at)}"
            for reminder in reminders[:10]
        ]
        if len(reminders) > 10:
            reminder_lines.append(f"Hay {len(reminders) - 10} recordatorios más pendientes.")
        return AssistantResponse(
            reply="Recordatorios pendientes:\n" + "\n".join(reminder_lines),
            metadata={
                "intent": IntentName.LIST_REMINDERS, "count": len(reminders),
                "reminders": [reminder.model_dump(mode="json") for reminder in reminders],
            },
        )

    def _mark_reminder_done(
        self, request: AssistantRequest, entities: dict[str, object]
    ) -> AssistantResponse:
        reminder_id = str(entities.get("reminder_id") or "").strip()
        title_query = str(entities.get("title_query") or "").strip()
        reminder = self._reminder_service.mark_done(
            business_id=request.business_id,
            reminder_id=reminder_id or None,
            title_query=title_query or None,
        )
        if reminder is None:
            return AssistantResponse(
                reply="No encontre un recordatorio pendiente para marcar como hecho.",
                metadata={"intent": IntentName.MARK_REMINDER_DONE},
            )

        return AssistantResponse(
            reply=f"Perfecto, marque como hecho: {reminder.title}.",
            actions=[
                AssistantAction(
                    type="reminder.completed",
                    payload={"reminder_id": reminder.id, "title": reminder.title},
                )
            ],
            metadata={"intent": IntentName.MARK_REMINDER_DONE},
        )

    def _get_operations_dashboard(
        self, request: AssistantRequest, intent: IntentName
    ) -> AssistantResponse:
        if self._operations_service is None:
            return self._operations_unavailable(intent)

        dashboard = self._operations_service.build_dashboard(business_id=request.business_id)
        top_product = (
            dashboard.top_products_by_quantity[0] if dashboard.top_products_by_quantity else None
        )
        top_product_text = ""
        if top_product is not None:
            product_name = top_product.product_name or top_product.external_product_id or "producto"
            top_product_text = f"\nMas vendido: {product_name} ({top_product.quantity_sold})."

        return AssistantResponse(
            reply=(
                f"Panel general del {dashboard.date_from:%d/%m} al {dashboard.date_to:%d/%m}:\n"
                f"- Ventas: ${dashboard.sales_totals.revenue} "
                f"({format_number(dashboard.sales_totals.quantity_sold)} cantidad registrada)\n"
                f"- Recordatorios pendientes: {len(dashboard.pending_reminders)}\n"
                f"- Alertas activas: {len(dashboard.alerts)}\n"
                f"- Ofertas de proveedores recientes: {len(dashboard.supplier_offers)}"
                f"{top_product_text}"
            ),
            metadata={
                "intent": intent,
                "date_from": dashboard.date_from.isoformat(),
                "date_to": dashboard.date_to.isoformat(),
                "alert_count": len(dashboard.alerts),
            },
        )

    def _list_operational_alerts(
        self, request: AssistantRequest, intent: IntentName
    ) -> AssistantResponse:
        if self._operations_service is None:
            return self._operations_unavailable(intent)

        dashboard = self._operations_service.build_dashboard(business_id=request.business_id)
        if not dashboard.alerts:
            return AssistantResponse(
                reply="No hay alertas operativas activas.",
                metadata={"intent": intent, "count": 0},
            )

        alert_lines = [f"- {alert.title}: {alert.description}" for alert in dashboard.alerts[:5]]
        return AssistantResponse(
            reply="Alertas operativas:\n" + "\n".join(alert_lines),
            metadata={"intent": intent, "count": len(dashboard.alerts)},
        )

    def _get_daily_summary(
        self, request: AssistantRequest, intent: IntentName
    ) -> AssistantResponse:
        if self._operations_service is None:
            return self._operations_unavailable(intent)

        today = request.timestamp.astimezone(ASSISTANT_TIMEZONE).date()
        summary = self._operations_service.build_daily_summary(
            business_id=request.business_id, date_from=today, date_to=today
        )
        dashboard = summary.dashboard
        reply = (
            f"Resumen del {today:%d/%m/%Y}.\n"
            + (
                f"Ventas registradas: ${format_number(dashboard.sales_totals.revenue)}.\n"
                if dashboard.sales_totals.line_count
                else "No hay ventas cargadas para este día; no puedo confirmar cuánto vendiste.\n"
            )
            + f"Recordatorios pendientes: {len(dashboard.pending_reminders)}. "
            + f"Alertas activas: {len(dashboard.alerts)}."
        )
        return AssistantResponse(
            reply=reply,
            metadata={
                "intent": intent,
                "date_from": summary.date_from.isoformat(),
                "date_to": summary.date_to.isoformat(),
            },
        )

    @staticmethod
    def _operations_unavailable(intent: IntentName) -> AssistantResponse:
        return AssistantResponse(
            reply="Para consultar el panel operativo necesito tener la base de datos configurada.",
            metadata={"intent": intent, "error": "database_unavailable"},
        )

    def _execute_sales_top_products(
        self,
        request: AssistantRequest,
        entities: dict[str, object],
        tool_name: AssistantToolName = AssistantToolName.SALES_TOP_PRODUCTS,
        intent: IntentName = IntentName.QUERY_SALES_TOP_PRODUCTS,
    ) -> AssistantResponse:
        if self._tool_registry is None:
            return AssistantResponse(
                reply="Para consultar ventas necesito tener la base de datos configurada.",
                metadata={
                    "intent": intent,
                    "error": "database_unavailable",
                },
            )
        try:
            result = self._tool_registry.execute(
                name=tool_name,
                arguments=entities,
                context=ToolExecutionContext(
                    business_id=request.business_id,
                    external_user_id=request.external_user_id,
                    channel=str(request.channel),
                    timestamp=request.timestamp,
                    text=request.text,
                ),
            )
        except (ValidationError, ValueError):
            if tool_name == AssistantToolName.SALES_TOP_PRODUCTS:
                limit = entities.get("limit")
                if isinstance(limit, int) and limit > 100:
                    return AssistantResponse(
                        reply="Puedo mostrar hasta 100 productos por mensaje. "
                        "Pedime los primeros 100 y después «mostrame 100 más».",
                        metadata={"intent": intent, "requires_clarification": True},
                    )
            return AssistantResponse(
                reply="No pude interpretar esos filtros o fechas. ¿Podés precisarlos?",
                metadata={"intent": intent, "requires_clarification": True},
            )
        logging.getLogger("uvicorn.error").info(
            "Assistant tool=%s period=%s..%s limit=%s growth=%s clarification=%s",
            result.name, result.data.get("date_from"), result.data.get("date_to"),
            result.data.get("limit"), result.data.get("growth", False),
            bool(result.metadata.get("requires_clarification")),
        )
        scope = sales_scope(result.name, entities, result.data)
        return AssistantResponse(
            reply=result.reply,
            requires_confirmation=bool(result.metadata.get("requires_clarification")),
            actions=[
                AssistantAction(
                    type="tool.executed",
                    payload={
                        "tool_name": result.name,
                        "metadata": result.metadata,
                        "data": result.data,
                    },
                )
            ],
            metadata={
                "intent": intent,
                "tool": result.name,
                "tool_metadata": result.metadata,
                "tool_data": result.data,
                **({"sales_scope": scope} if scope else {}),
            },
        )

    @staticmethod
    def _is_greeting_or_help(text: str | None) -> bool:
        from app.modules.procurement.normalization import normalize_text

        normalized = normalize_text(text or "").strip(" ¿?¡!.,")
        return normalized in {
            "hola", "buen dia", "buenas", "buenas tardes", "buenas noches", "ayuda",
            "que podes hacer", "que puedes hacer", "como me podes ayudar",
        }

    @staticmethod
    def _parse_due_at(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        return datetime.fromisoformat(value)

    @staticmethod
    def _format_due_text(due_at: datetime | None) -> str:
        if due_at is None:
            return ""
        return f" para {due_at.astimezone(ASSISTANT_TIMEZONE):%d/%m %H:%M}"

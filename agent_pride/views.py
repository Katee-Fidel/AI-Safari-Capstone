"""
agent_pride/views.py
====================
Django REST Framework integration layer for the Agent Pride pipeline.

Exposes a single endpoint:
  POST /api/v1/pipeline/ingest/

The view is intentionally thin -- all business logic lives in
AgentPridePipeline (services.py). The view is responsible only for:
  - Request parsing and basic structural validation.
  - Exception mapping from service-layer errors to HTTP responses.
  - Response serialisation.

URL registration (add to your project's urls.py):
  from django.urls import path, include
  from agent_pride.views import PipelineIngestView

  urlpatterns = [
      path("api/v1/pipeline/ingest/", PipelineIngestView.as_view(), name="pipeline-ingest"),
  ]
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from rest_framework import status
from rest_framework.permissions import AllowAny

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ExecutionLedger

logger = logging.getLogger(__name__)


def home(request):
    """Render the simple nontechnical pipeline interface."""
    return render(request, "agent_pride/home.html")


class PipelineIngestView(APIView):
    """
    POST /api/v1/pipeline/ingest/

    Accepts a raw financial telemetry payload, executes the 5-node
    Agent Pride pipeline, and returns the ledger_id of the persisted
    execution record.

    Authentication / throttling should be configured via DRF settings
    or by setting ``authentication_classes`` and ``throttle_classes``
    on this view in production.

    Request body (JSON):
        Any flat JSON object containing financial telemetry fields.
        PII fields (phone_number, national_id, etc.) are accepted here
        but are stripped by Node 1 before any processing occurs.

        Minimum required fields after PII stripping:
          balance_tier               int   (1-10)
          velocity_score             float (0-100)
          frequency_multiplier       float (0.1-50)
          avg_transaction_size_tier  int   (1-10)
          mpesa_active_days_last_30  int   (0-30)
          bill_payment_regularity    float (0-1)

        Optional fields:
          chama_participation_flag   bool  (default: false)
          chama_contribution_tier    int   (0-10, default: 0)
          source_channel             str   (default: "api")

    Response (HTTP 202 Accepted):
        {
            "ledger_id": "<uuid>",
            "session_message": "Pipeline executed successfully.",
            "status": "ACCEPTED"
        }

    Error responses:
        400 Bad Request  — Payload is missing or structurally empty.
        422 Unprocessable Entity — PII sanitisation or classification failed.
        500 Internal Server Error — Unexpected pipeline failure.
        503 Service Unavailable — CrewAI or persistence layer failure.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        """
        Handle a POST request to execute the Agent Pride pipeline.

        Args:
            request: DRF Request object containing the raw payload as JSON.

        Returns:
            DRF Response with ledger_id on success, or an error body on failure.
        """
        raw_payload = request.data

        # Guard against completely empty or non-dict payloads.
        if not raw_payload or not isinstance(raw_payload, dict):
            logger.warning(
                "PipelineIngestView received an empty or non-dict payload. "
                "Remote addr: %s",
                request.META.get("REMOTE_ADDR", "unknown"),
            )
            return Response(
                {
                    "error": "Invalid payload.",
                    "detail": (
                        "Request body must be a non-empty JSON object containing "
                        "financial telemetry fields."
                    ),
                    "status": "BAD_REQUEST",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .services import (  # noqa: PLC0415
            AgentPridePipeline,
            ClassificationError,
            CrewExecutionError,
            PersistenceError,
            PipelineError,
            SanitisationError,
        )

        pipeline = AgentPridePipeline()

        try:
            ledger_id: uuid.UUID = pipeline.execute_workflow(raw_payload)

        except SanitisationError as exc:
            logger.warning(
                "PipelineIngestView: Sanitisation error — %s", exc,
                extra={"payload_keys": list(raw_payload.keys())},
            )
            return Response(
                {
                    "error": "Payload sanitisation failed.",
                    "detail": str(exc),
                    "status": "UNPROCESSABLE",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        except ClassificationError as exc:
            logger.warning("PipelineIngestView: Classification error — %s", exc)
            return Response(
                {
                    "error": "Telemetry classification failed.",
                    "detail": str(exc),
                    "status": "UNPROCESSABLE",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        except CrewExecutionError as exc:
            logger.error("PipelineIngestView: Crew execution error — %s", exc)
            detail = (
                str(exc)
                if settings.DEBUG
                else (
                    "The CrewAI processing core encountered an error. "
                    "Please retry the request."
                )
            )
            return Response(
                {
                    "error": "AI agent orchestration failed.",
                    "detail": detail,
                    "status": "SERVICE_UNAVAILABLE",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        except PersistenceError as exc:
            logger.error("PipelineIngestView: Persistence error — %s", exc)
            return Response(
                {
                    "error": "Ledger persistence failed.",
                    "detail": (
                        "The pipeline completed but the result could not be saved. "
                        "Please retry the request."
                    ),
                    "status": "SERVICE_UNAVAILABLE",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        except PipelineError as exc:
            logger.error("PipelineIngestView: Generic pipeline error — %s", exc)
            return Response(
                {
                    "error": "Pipeline execution failed.",
                    "detail": str(exc),
                    "status": "INTERNAL_ERROR",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        except Exception as exc:  # pragma: no cover
            logger.exception("PipelineIngestView: Unhandled exception.")
            return Response(
                {
                    "error": "An unexpected internal error occurred.",
                    "detail": (
                        "Please contact platform support if this error persists."
                    ),
                    "status": "INTERNAL_ERROR",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "PipelineIngestView: Pipeline completed successfully. ledger_id=%s",
            ledger_id,
        )
        return Response(
            {
                "ledger_id": str(ledger_id),
                "session_message": "Pipeline executed successfully.",
                "status": "ACCEPTED",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class LedgerResultView(APIView):
    """Return a concise, user-safe view of a completed pipeline run."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request, ledger_id: uuid.UUID) -> Response:
        ledger = get_object_or_404(ExecutionLedger, ledger_id=ledger_id)
        return Response(
            {
                "ledger_id": str(ledger.ledger_id),
                "status": ledger.status,
                "income_pattern": ledger.income_pattern,
                "risk_tier": ledger.risk_tier,
                "fallback_applied": ledger.fallback_applied,
                "overall_default_risk_pct": float(ledger.overall_default_risk_pct),
                "max_leverage_ratio": float(ledger.max_leverage_ratio),
                "sentinel_verdict": ledger.sentinel_verdict,
                "recommendations": ledger.final_recommendations_json,
                "compliance_checks": ledger.compliance_checks_json,
                "created_at": ledger.created_at.isoformat(),
            }
        )

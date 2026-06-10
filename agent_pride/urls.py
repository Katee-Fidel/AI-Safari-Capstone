from django.urls import path
from .views import LedgerResultView, PipelineIngestView, home

app_name = "agent_pride"

urlpatterns = [
    path("", home, name="home"),
    path(
        "api/v1/pipeline/ingest/",
        PipelineIngestView.as_view(),
        name="pipeline-ingest",
    ),
    path(
        "api/v1/ledger/<uuid:ledger_id>/",
        LedgerResultView.as_view(),
        name="ledger-result",
    ),
]


from django.urls import path
from .views import PipelineIngestView

app_name = "agent_pride"

urlpatterns = [
	path(
		"api/v1/pipeline/ingest/",
		PipelineIngestView.as_view(),
		name="pipeline-ingest",
	),
]


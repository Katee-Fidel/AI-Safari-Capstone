from django.contrib import admin
from .models import AssetOpportunity, ExecutionLedger, IngestionTelemetry


@admin.register(IngestionTelemetry)
class IngestionTelemetryAdmin(admin.ModelAdmin):
	list_display = ("session_id", "balance_tier", "velocity_score", "created_at")
	list_filter = ("chama_participation_flag", "source_channel")
	readonly_fields = ("session_id", "raw_schema_hash", "created_at", "updated_at")
	search_fields = ("session_id",)


@admin.register(AssetOpportunity)
class AssetOpportunityAdmin(admin.ModelAdmin):
	list_display = (
		"provider_name", "asset_class", "annualised_yield_pct",
		"default_risk_pct", "is_active",
	)
	list_filter = ("asset_class", "is_active")
	list_editable = ("is_active",)
	search_fields = ("provider_name",)


@admin.register(ExecutionLedger)
class ExecutionLedgerAdmin(admin.ModelAdmin):
	list_display = (
		"ledger_id", "session_id", "status", "risk_tier",
		"fallback_applied", "created_at",
	)
	list_filter = ("status", "risk_tier", "fallback_applied", "income_pattern")
	readonly_fields = tuple(
		f.name for f in ExecutionLedger._meta.get_fields()
	)
	search_fields = ("session_id", "ledger_id")

	def has_add_permission(self, request):
		return False  # Ledger is append-only; block manual creation via admin.

	def has_change_permission(self, request, obj=None):
		return False  # Ledger is immutable.


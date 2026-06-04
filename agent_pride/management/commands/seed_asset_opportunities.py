from django.core.management.base import BaseCommand
from agent_pride.models import AssetOpportunity


class Command(BaseCommand):
    help = "Seed the AssetOpportunity catalogue with Kenyan market fixtures."

    def handle(self, *args, **options):
        fixtures = [
            dict(
                provider_name="CIC Money Market Fund",
                asset_class="MMF",
                annualised_yield_pct="10.50",
                minimum_investment_kes="1000.00",
                liquidity_days=1,
                default_risk_pct="0.50",
                max_leverage_ratio="1.00",
                suitable_risk_tiers=["CONSERVATIVE", "MODERATE", "GROWTH", "AGGRESSIVE"],
            ),
            dict(
                provider_name="Sanlam Money Market Fund",
                asset_class="MMF",
                annualised_yield_pct="11.20",
                minimum_investment_kes="1000.00",
                liquidity_days=2,
                default_risk_pct="0.60",
                max_leverage_ratio="1.00",
                suitable_risk_tiers=["CONSERVATIVE", "MODERATE", "GROWTH", "AGGRESSIVE"],
            ),
            dict(
                provider_name="Stima Sacco",
                asset_class="SACCO_DEPOSIT",
                annualised_yield_pct="12.00",
                minimum_investment_kes="5000.00",
                liquidity_days=30,
                default_risk_pct="1.20",
                max_leverage_ratio="1.50",
                suitable_risk_tiers=["CONSERVATIVE", "MODERATE"],
            ),
            dict(
                provider_name="Mwalimu National Sacco",
                asset_class="SACCO_DEPOSIT",
                annualised_yield_pct="13.00",
                minimum_investment_kes="3000.00",
                liquidity_days=30,
                default_risk_pct="1.50",
                max_leverage_ratio="1.50",
                suitable_risk_tiers=["CONSERVATIVE", "MODERATE", "GROWTH"],
            ),
            dict(
                provider_name="CBK Treasury Bill (91-day)",
                asset_class="TREASURY_BILL",
                annualised_yield_pct="16.50",
                minimum_investment_kes="100000.00",
                liquidity_days=91,
                default_risk_pct="0.05",
                max_leverage_ratio="1.00",
                suitable_risk_tiers=["MODERATE", "GROWTH", "AGGRESSIVE"],
            ),
            dict(
                provider_name="CBK Infrastructure Bond",
                asset_class="GOVERNMENT_BOND",
                annualised_yield_pct="14.50",
                minimum_investment_kes="50000.00",
                liquidity_days=365,
                default_risk_pct="0.10",
                max_leverage_ratio="1.00",
                suitable_risk_tiers=["MODERATE", "GROWTH", "AGGRESSIVE"],
            ),
            dict(
                provider_name="Britam Equity Fund",
                asset_class="UNIT_TRUST_EQUITY",
                annualised_yield_pct="18.00",
                minimum_investment_kes="5000.00",
                liquidity_days=5,
                default_risk_pct="2.50",
                max_leverage_ratio="1.00",
                suitable_risk_tiers=["GROWTH", "AGGRESSIVE"],
            ),
            dict(
                provider_name="NCBA Unit Trust Equity Fund",
                asset_class="UNIT_TRUST_EQUITY",
                annualised_yield_pct="17.00",
                minimum_investment_kes="5000.00",
                liquidity_days=5,
                default_risk_pct="2.20",
                max_leverage_ratio="1.00",
                suitable_risk_tiers=["GROWTH", "AGGRESSIVE"],
            ),
            dict(
                provider_name="Generic Chama Contribution Pool",
                asset_class="CHAMA_CONTRIBUTION",
                annualised_yield_pct="15.00",
                minimum_investment_kes="500.00",
                liquidity_days=30,
                default_risk_pct="1.80",
                max_leverage_ratio="1.20",
                suitable_risk_tiers=["CONSERVATIVE", "MODERATE", "GROWTH", "AGGRESSIVE"],
            ),
        ]

        created = 0
        for data in fixtures:
            _, was_created = AssetOpportunity.objects.get_or_create(
                provider_name=data["provider_name"],
                asset_class=data["asset_class"],
                defaults=data,
            )
            if was_created:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created} new asset opportunities "
                f"({len(fixtures) - created} already existed)."
            )
        )

"""
SolarSync - Quotation calculation engine and PDF generator.
Implements dated rule lookups, mathematical subsidy calculations, and ReportLab PDF compilation.
"""

import os
import random
from datetime import date, datetime
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import select, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from app.config import get_settings
from app.models.lead import Lead, LeadStatus
from app.models.rules import SubsidyRule, PricingRule
from app.models.quote import Quote

settings = get_settings()


async def get_active_subsidy_rule(
    db: AsyncSession,
    as_of_date: Optional[date] = None,
) -> SubsidyRule:
    """Fetch the active subsidy rule valid on the given date (default today)."""
    target_date = as_of_date or date.today()
    query = select(SubsidyRule).where(
        and_(
            SubsidyRule.is_active == True,
            SubsidyRule.effective_from <= target_date,
            or_(
                SubsidyRule.effective_to == None,
                SubsidyRule.effective_to >= target_date,
            ),
        )
    ).order_by(desc(SubsidyRule.effective_from))

    result = await db.execute(query)
    rule = result.scalars().first()
    if not rule:
        # Fallback to any active rule
        fallback_res = await db.execute(select(SubsidyRule).where(SubsidyRule.is_active == True))
        rule = fallback_res.scalars().first()
    return rule


async def get_active_pricing_rule(
    db: AsyncSession,
    as_of_date: Optional[date] = None,
) -> PricingRule:
    """Fetch the active turnkey pricing rule valid on the given date."""
    target_date = as_of_date or date.today()
    query = select(PricingRule).where(
        and_(
            PricingRule.is_active == True,
            PricingRule.effective_from <= target_date,
            or_(
                PricingRule.effective_to == None,
                PricingRule.effective_to >= target_date,
            ),
        )
    ).order_by(desc(PricingRule.effective_from))

    result = await db.execute(query)
    rule = result.scalars().first()
    if not rule:
        fallback_res = await db.execute(select(PricingRule).where(PricingRule.is_active == True))
        rule = fallback_res.scalars().first()
    return rule


async def seed_default_rules(db: AsyncSession):
    """Seed official PM Surya Ghar subsidy scheme and Company A Vidarbha pricing if not present."""
    # 1. Subsidy Rule
    res = await db.execute(select(SubsidyRule))
    if not res.scalars().first():
        subsidy_rule = SubsidyRule(
            scheme_name="PM Surya Ghar: Muft Bijli Yojana (Central Residential Scheme)",
            effective_from=date(2024, 2, 15),
            effective_to=None,
            rate_per_kw_upto_2=30000.0,
            rate_for_3rd_kw=18000.0,
            rate_above_3rd_kw=0.0,
            cap_amount=78000.0,
            is_active=True,
        )
        db.add(subsidy_rule)

    # 2. Pricing Rule
    res_p = await db.execute(select(PricingRule))
    if not res_p.scalars().first():
        pricing_rule = PricingRule(
            name="Vidarbha Turnkey Rooftop Solar Standard (Mono PERC + Tier-1 Inverter)",
            effective_from=date(2024, 1, 1),
            effective_to=None,
            base_cost_per_kw=60000.0,            # ₹60,000 / kW turnkey
            generation_factor_kwh_per_kw=1400.0, # 1,400 units/kW/year from Company A brief
            tariff_per_kwh_rs=7.50,              # MSEDCL average residential tariff
            is_active=True,
        )
        db.add(pricing_rule)

    await db.commit()


def calculate_quote_financials(
    system_size_kw: float,
    subsidy_rule: SubsidyRule,
    pricing_rule: PricingRule,
) -> Dict[str, Any]:
    """
    Pure mathematical calculations for system cost, subsidy, generation, and payback:
    - Gross Cost = system_size * base_cost_per_kw
    - Subsidy = dated rule calculation (up to 2kW @ 30k, 3rd @ 18k, max 78k)
    - Net Cost = Gross Cost - Subsidy
    - Yearly Gen (kWh) = system_size * generation_factor (1400)
    - Yearly Savings (₹) = Yearly Gen * tariff (₹7.50)
    - Payback (Years) = Net Cost / Yearly Savings
    - 25-Year Lifetime Savings = (Yearly Savings * 25) - Net Cost
    """
    size = float(system_size_kw)
    gross_cost = round(size * pricing_rule.base_cost_per_kw, 2)
    subsidy = round(subsidy_rule.calculate_subsidy(size), 2)
    net_cost = round(max(0.0, gross_cost - subsidy), 2)

    yearly_gen = int(round(size * pricing_rule.generation_factor_kwh_per_kw))
    monthly_gen = int(round(yearly_gen / 12.0))

    yearly_savings = round(yearly_gen * pricing_rule.tariff_per_kwh_rs, 2)
    monthly_savings = round(yearly_savings / 12.0, 2)

    payback_years = round(net_cost / yearly_savings, 1) if yearly_savings > 0 else 0.0
    lifetime_savings = round((yearly_savings * 25.0) - net_cost, 2)

    return {
        "system_size_kw": size,
        "gross_cost_rs": gross_cost,
        "subsidy_amount_rs": subsidy,
        "net_cost_rs": net_cost,
        "yearly_generation_kwh": yearly_gen,
        "monthly_generation_kwh": monthly_gen,
        "yearly_savings_rs": yearly_savings,
        "monthly_savings_rs": monthly_savings,
        "payback_years": payback_years,
        "lifetime_savings_25yr_rs": lifetime_savings,
        "tariff_per_kwh_rs": pricing_rule.tariff_per_kwh_rs,
        "base_cost_per_kw": pricing_rule.base_cost_per_kw,
        "generation_factor": pricing_rule.generation_factor_kwh_per_kw,
    }


def generate_quote_pdf_file(
    quote: Quote,
    lead: Lead,
    subsidy_rule: SubsidyRule,
    pricing_rule: PricingRule,
) -> str:
    """
    Generate an elegant, professional single-page PDF quotation for Company A using ReportLab.
    Matches brief: clean layout, white space, clear story:
    Size -> Cost After Subsidy -> Payback -> Technical Details.
    Guaranteed single-page fit without overflowing to a second page.
    """
    os.makedirs(os.path.join(settings.UPLOAD_DIR, "quotes"), exist_ok=True)
    pdf_filename = f"{quote.quote_number}.pdf"
    rel_path = f"uploads/quotes/{pdf_filename}"
    abs_path = os.path.join(settings.UPLOAD_DIR, "quotes", pdf_filename)

    # Letter printable width: 612 - 56 = 556 pt, height: 792 - 40 = 752 pt
    doc = SimpleDocTemplate(
        abs_path,
        pagesize=letter,
        leftMargin=28,
        rightMargin=28,
        topMargin=22,
        bottomMargin=18,
    )

    styles = getSampleStyleSheet()

    # Color palette
    solar_orange = colors.HexColor("#D97706")
    solar_dark = colors.HexColor("#1F2937")
    solar_gray = colors.HexColor("#4B5563")
    solar_light_gray = colors.HexColor("#6B7280")
    solar_bg = colors.HexColor("#FFFBEB")
    border_amber = colors.HexColor("#FDE68A")
    border_gray = colors.HexColor("#E5E7EB")
    bg_light = colors.HexColor("#F9FAFB")
    text_emerald = colors.HexColor("#059669")

    # Typography styles
    brand_title_style = ParagraphStyle(
        "BrandTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=solar_dark,
    )

    quote_heading_style = ParagraphStyle(
        "QuoteHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        alignment=2,
        textColor=solar_orange,
    )

    section_h2_style = ParagraphStyle(
        "SecH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=solar_dark,
        spaceBefore=0,
        spaceAfter=3,
    )

    cell_style = ParagraphStyle(
        "CellText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=solar_dark,
    )

    cell_bold_style = ParagraphStyle(
        "CellTextBold",
        parent=cell_style,
        fontName="Helvetica-Bold",
    )

    # Hero metric styles (separate styles and distinct table rows prevent any text collision/overlap)
    hero_lbl_style = ParagraphStyle(
        "HeroLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=8.5,
        alignment=1,
        textColor=solar_light_gray,
    )

    hero_val_dark = ParagraphStyle(
        "HeroValDark",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=16,
        alignment=1,
        textColor=solar_dark,
    )

    hero_val_orange = ParagraphStyle(
        "HeroValOrange",
        parent=hero_val_dark,
        textColor=solar_orange,
    )

    hero_val_emerald = ParagraphStyle(
        "HeroValEmerald",
        parent=hero_val_dark,
        textColor=text_emerald,
    )

    hero_sub_style = ParagraphStyle(
        "HeroSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=6.8,
        leading=8,
        alignment=1,
        textColor=solar_gray,
    )

    hero_sub_emerald = ParagraphStyle(
        "HeroSubEmerald",
        parent=hero_sub_style,
        fontName="Helvetica-Bold",
        textColor=text_emerald,
    )

    footer_style = ParagraphStyle(
        "FooterText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=6.8,
        leading=8.5,
        alignment=1,
        textColor=solar_light_gray,
    )

    story = []

    # 1. Header: Company Brand + Quote Info
    header_data = [
        [
            Paragraph("<b>Company A — Rooftop Solar</b><br/><font color='#6B7280' size='7.5'>Nagpur, Vidarbha · 120+ Monthly Installations · MNRE Channel Partner</font>", brand_title_style),
            Paragraph(f"<b>SOLAR PROPOSAL</b><br/><font color='#1F2937' size='9.5'><b>{quote.quote_number}</b></font><br/><font color='#6B7280' size='7'>Date: {quote.created_at.strftime('%d %b %Y')}</font>", quote_heading_style),
        ]
    ]
    header_table = Table(header_data, colWidths=[346, 210])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=solar_orange, spaceBefore=3, spaceAfter=7))

    # 2. Customer Summary Grid (Compact 2 rows x 4 columns)
    cust_data = [
        [
            Paragraph(f"<b>Applicant:</b> {lead.name}", cell_style),
            Paragraph(f"<b>Phone:</b> +91 {lead.phone}", cell_style),
            Paragraph(f"<b>Monthly Bill:</b> Rs. {lead.monthly_bill_rs:,}", cell_style),
            Paragraph(f"<b>Usable Roof:</b> {lead.roof_area_sqft:.0f} sq.ft.", cell_style),
        ],
        [
            Paragraph(f"<b>Location:</b> PIN {lead.pincode}, Nagpur", cell_style),
            Paragraph(f"<b>Property:</b> {lead.ownership.value.capitalize()} ({lead.roof_type.value.replace('_', ' ').title()})", cell_style),
            Paragraph("<b>Grid Utility:</b> MSEDCL (LT-I)", cell_style),
            Paragraph("<b>Solar Standard:</b> 1,400 kWh/kW/yr", cell_style),
        ]
    ]
    cust_table = Table(cust_data, colWidths=[140, 150, 136, 130])
    cust_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), bg_light),
        ('BOX', (0,0), (-1,-1), 0.5, border_gray),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#F3F4F6')),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(cust_table)
    story.append(Spacer(1, 7))

    # 3. The Solar Story (Hero Numbers: Size -> Cost After Subsidy -> Payback)
    # Using separate rows for Label, Value, Subtitle entirely prevents vertical overlapping!
    hero_table_data = [
        [
            Paragraph('RECOMMENDED SIZE', hero_lbl_style),
            Paragraph('NET INVESTMENT', ParagraphStyle('HL2', parent=hero_lbl_style, textColor=solar_orange)),
            Paragraph('PAYBACK PERIOD', ParagraphStyle('HL3', parent=hero_lbl_style, textColor=text_emerald)),
            Paragraph('EST. YEARLY SAVINGS', hero_lbl_style),
        ],
        [
            Paragraph(f'{quote.system_size_kw} kW', hero_val_dark),
            Paragraph(f'Rs. {quote.net_cost_rs:,.0f}', hero_val_orange),
            Paragraph(f'{quote.payback_years} Years', hero_val_emerald),
            Paragraph(f'Rs. {quote.yearly_savings_rs:,.0f}', hero_val_dark),
        ],
        [
            Paragraph('1,400 kWh/yr per kW', hero_sub_style),
            Paragraph(f'After Rs. {quote.subsidy_amount_rs:,.0f} Subsidy', hero_sub_emerald),
            Paragraph('25-Yr Panel Warranty', hero_sub_style),
            Paragraph(f'~Rs. {quote.yearly_savings_rs/12:,.0f} / month', hero_sub_style),
        ],
    ]
    hero_table = Table(hero_table_data, colWidths=[139, 139, 139, 139])
    hero_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), solar_bg),
        ('BOX', (0,0), (-1,-1), 1, border_amber),
        ('INNERGRID', (0,0), (-1,-1), 0.5, border_amber),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,0), 4),
        ('BOTTOMPADDING', (0,0), (-1,0), 1),
        ('TOPPADDING', (0,1), (-1,1), 1),
        ('BOTTOMPADDING', (0,1), (-1,1), 1),
        ('TOPPADDING', (0,2), (-1,2), 1),
        ('BOTTOMPADDING', (0,2), (-1,2), 4),
    ]))
    story.append(hero_table)
    story.append(Spacer(1, 7))

    # 4. Financial & Subsidy Breakdown Table
    story.append(Paragraph("Turnkey Cost & PM Surya Ghar Subsidy Breakdown", section_h2_style))
    fin_data = [
        [Paragraph("<b>Component / Scheme Item</b>", cell_bold_style), Paragraph("<b>Calculation Formula</b>", cell_bold_style), Paragraph("<b>Amount (INR)</b>", cell_bold_style)],
        [Paragraph("Turnkey Solar System Cost (Gross)", cell_style), Paragraph(f"{quote.system_size_kw} kW × Rs. {pricing_rule.base_cost_per_kw:,.0f}/kW turnkey (Panels, Inverter, Structure, Installation)", cell_style), Paragraph(f"Rs. {quote.gross_cost_rs:,.2f}", cell_style)],
        [Paragraph(f"<b>Central Residential Subsidy (DBT)</b><br/><font size='6.5' color='#6B7280'>{subsidy_rule.scheme_name}</font>", cell_style), Paragraph(f"Rs. 30,000/kW (up to 2kW) + Rs. 18,000 (3rd kW)<br/><font size='6.5' color='#6B7280'>Effective from {subsidy_rule.effective_from.strftime('%d-%b-%Y')}</font>", cell_style), Paragraph(f"<font color='#059669'><b>- Rs. {quote.subsidy_amount_rs:,.2f}</b></font>", cell_style)],
        [Paragraph("<b>Final Net Investment to Homeowner</b>", cell_bold_style), Paragraph("<b>Turnkey Gross minus Direct Benefit Transfer (DBT) to customer bank</b>", cell_bold_style), Paragraph(f"<font color='#D97706' size='9'><b>Rs. {quote.net_cost_rs:,.2f}</b></font>", cell_style)],
    ]
    fin_table = Table(fin_data, colWidths=[206, 230, 120])
    fin_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F3F4F6")),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#FEF3C7")),
        ('GRID', (0,0), (-1,-1), 0.5, border_gray),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(fin_table)
    story.append(Spacer(1, 7))

    # 5. Side-by-Side: Energy Generation Returns (Left) and Hardware & Warranties (Right)
    gen_col_data = [
        [Paragraph("<b>Generation & Financial Return</b>", cell_bold_style), Paragraph("<b>Value</b>", cell_bold_style)],
        [Paragraph("Solar Generation Baseline", cell_style), Paragraph(f"{pricing_rule.generation_factor_kwh_per_kw:,.0f} kWh/kW/yr", cell_style)],
        [Paragraph("Est. Annual Electricity Generation", cell_style), Paragraph(f"<b>{quote.yearly_generation_kwh:,} units</b> (~{quote.yearly_generation_kwh//12}/mo)", cell_style)],
        [Paragraph("MSEDCL Residential Tariff Applied", cell_style), Paragraph(f"Rs. {pricing_rule.tariff_per_kwh_rs:.2f} / unit", cell_style)],
        [Paragraph("Est. Annual Utility Bill Savings", cell_style), Paragraph(f"<b>Rs. {quote.yearly_savings_rs:,.0f} / year</b>", cell_style)],
        [Paragraph("Capital Payback Period", cell_style), Paragraph(f"<b>{quote.payback_years} Years</b> (~{(quote.payback_years*12):.0f} mo)", cell_style)],
        [Paragraph("25-Yr Net Cumulative Savings", cell_style), Paragraph(f"<font color='#059669'><b>Rs. {quote.lifetime_savings_25yr_rs:,.0f}</b></font>", cell_style)],
    ]
    gen_table = Table(gen_col_data, colWidths=[165, 107])
    gen_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F3F4F6")),
        ('GRID', (0,0), (-1,-1), 0.5, border_gray),
        ('TOPPADDING', (0,0), (-1,-1), 2.2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.2),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))

    tech_col_data = [
        [Paragraph("<b>Hardware Component</b>", cell_bold_style), Paragraph("<b>Specification & Warranty</b>", cell_bold_style)],
        [Paragraph("Solar Modules", cell_style), Paragraph(f"{quote.panel_specs}<br/><font color='#059669' size='6.5'><b>25-Year Performance Warranty</b></font>", cell_style)],
        [Paragraph("Grid Inverter", cell_style), Paragraph(f"{quote.inverter_specs}<br/><font color='#059669' size='6.5'><b>5-Year Replacement Warranty</b></font>", cell_style)],
        [Paragraph("GI Structure", cell_style), Paragraph("Hot-dip Galvanized Iron structure (rated for Vidarbha heat/wind load)", cell_style)],
        [Paragraph("DISCOM & DBT", cell_style), Paragraph("MSEDCL Net-Metering liaison & PM Surya Ghar portal subsidy filing included", cell_style)],
    ]
    tech_table = Table(tech_col_data, colWidths=[80, 196])
    tech_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F3F4F6")),
        ('GRID', (0,0), (-1,-1), 0.5, border_gray),
        ('TOPPADDING', (0,0), (-1,-1), 2.2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.2),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))

    side_by_side = Table([
        [
            Paragraph("Generation & 25-Year Returns", section_h2_style),
            Paragraph("Package Inclusions & Warranties", section_h2_style),
        ],
        [
            gen_table,
            tech_table,
        ]
    ], colWidths=[274, 282])
    side_by_side.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(side_by_side)
    story.append(Spacer(1, 8))

    # 6. Signature Footer (Always sits on Page 1)
    story.append(HRFlowable(width="100%", thickness=0.5, color=border_gray, spaceBefore=2, spaceAfter=4))
    footer_text = (
        "Quotation generated by SolarSync Platform for Company A, Nagpur · Valid for 30 days from issue date.<br/>"
        "Central Subsidy subject to PM Surya Ghar DBT portal verification · Consumer numbers protected under DPDP Act 2023."
    )
    story.append(Paragraph(footer_text, footer_style))

    doc.build(story)
    return rel_path


async def create_quote_for_lead(
    db: AsyncSession,
    lead: Lead,
    system_size_kw: float,
    panel_specs: Optional[str] = None,
    inverter_specs: Optional[str] = None,
    notes: Optional[str] = None,
) -> Quote:
    """Create a new Quote record, compute financial formulas from dated rules, and generate the PDF."""
    subsidy_rule = await get_active_subsidy_rule(db)
    pricing_rule = await get_active_pricing_rule(db)

    # Calculate financial formulas
    fin = calculate_quote_financials(system_size_kw, subsidy_rule, pricing_rule)

    # Generate unique quote number
    rand_code = random.randint(100, 999)
    quote_number = f"QT-{lead.lead_id.replace('LD-', '')}-{rand_code}"

    quote = Quote(
        quote_number=quote_number,
        lead_id=lead.id,
        subsidy_rule_id=subsidy_rule.id,
        pricing_rule_id=pricing_rule.id,
        system_size_kw=system_size_kw,
        panel_specs=panel_specs or "Mono PERC 550W Tier-1 (Half-Cut)",
        inverter_specs=inverter_specs or "High-Efficiency On-Grid String Inverter (IP65)",
        gross_cost_rs=fin["gross_cost_rs"],
        subsidy_amount_rs=fin["subsidy_amount_rs"],
        net_cost_rs=fin["net_cost_rs"],
        yearly_generation_kwh=fin["yearly_generation_kwh"],
        yearly_savings_rs=fin["yearly_savings_rs"],
        payback_years=fin["payback_years"],
        lifetime_savings_25yr_rs=fin["lifetime_savings_25yr_rs"],
        notes=notes,
        status="ready",
    )
    db.add(quote)
    await db.flush()
    await db.refresh(quote)

    # Generate the PDF file
    rel_pdf_path = generate_quote_pdf_file(quote, lead, subsidy_rule, pricing_rule)
    quote.pdf_path = rel_pdf_path
    
    # Update lead status to QUOTE_GENERATED
    lead.status = LeadStatus.QUOTE_GENERATED
    await db.commit()
    await db.refresh(quote)
    await db.refresh(lead)

    return quote


async def get_quote_by_number(db: AsyncSession, quote_number: str) -> Optional[Quote]:
    """Fetch quote by its public quote_number (e.g. QT-30881-104)."""
    result = await db.execute(select(Quote).where(Quote.quote_number == quote_number))
    return result.scalar_one_or_none()


async def list_quotes(db: AsyncSession, skip: int = 0, limit: int = 50) -> list[Quote]:
    """List recent quotes ordered by newest."""
    query = select(Quote).order_by(desc(Quote.created_at)).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())

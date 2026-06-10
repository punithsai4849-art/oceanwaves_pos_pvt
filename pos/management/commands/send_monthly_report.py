import datetime
import calendar
import io
import os
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum, Count, Q
from django.core.mail import EmailMessage
from django.contrib.auth.models import User
from pos.models import Store, Product, SaleItem, Sale, Expense, DailyStockSnapshot, StockLog

# openpyxl imports
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

class Command(BaseCommand):
    help = 'Generates a monthly sales, profit, and expenses report and emails it to admins.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--month',
            type=str,
            help='Target month in YYYY-MM format. Defaults to previous month.',
            default=''
        )

    def handle(self, *args, **options):
        # Calculate target month/year
        today = timezone.now().date()
        target_month_str = options['month']
        
        if target_month_str:
            try:
                year, month = map(int, target_month_str.split('-'))
            except ValueError:
                self.stderr.write("Invalid month format. Please use YYYY-MM.")
                return
        else:
            # Default to the previous month
            first_of_this_month = today.replace(day=1)
            last_day_of_prev_month = first_of_this_month - datetime.timedelta(days=1)
            year = last_day_of_prev_month.year
            month = last_day_of_prev_month.month

        month_name = datetime.date(year, month, 1).strftime('%B %Y')
        self.stdout.write(f"Generating monthly report for: {month_name}")

        # Get timezone-aware start and end timestamps
        tz = timezone.get_current_timezone()
        m_start = timezone.make_aware(datetime.datetime(year, month, 1, 0, 0, 0), tz)
        last_day = calendar.monthrange(year, month)[1]
        m_end = timezone.make_aware(datetime.datetime(year, month, last_day, 23, 59, 59), tz)
        target_date_range = (datetime.date(year, month, 1), datetime.date(year, month, last_day))

        # -------------------------------------------------------------
        # Gather consolidated data
        # -------------------------------------------------------------
        active_stores = list(Store.objects.filter(is_active=True))
        
        # 1. Total Sales and Profits
        sales_agg = SaleItem.objects.filter(
            sale__created_at__range=(m_start, m_end)
        ).aggregate(
            sales=Sum('total_amount'),
            cost=Sum('total_cost'),
            profit=Sum('profit')
        )
        total_sales = float(sales_agg['sales'] or 0)
        total_cost = float(sales_agg['cost'] or 0)
        gross_profit = float(sales_agg['profit'] or 0)

        # 2. Operating Expenses vs Stock Purchases
        expenses_qs = Expense.objects.filter(date__year=year, date__month=month)
        
        # Total overall expenses
        total_expenses = float(expenses_qs.aggregate(t=Sum('amount'))['t'] or 0)
        
        # Stock Purchase expenses
        stock_purchases = float(expenses_qs.filter(category='PURCHASE').aggregate(t=Sum('amount'))['t'] or 0)
        
        # Operating expenses (rent, electricity, salaries, transport, maintenance, misc, other)
        operating_expenses = total_expenses - stock_purchases
        
        # Net Profit
        net_profit = gross_profit - total_expenses
        profit_margin = (gross_profit / total_sales * 100) if total_sales else 0
        net_margin = (net_profit / total_sales * 100) if total_sales else 0

        # Store-wise breakdown
        store_stats = []
        for store in active_stores:
            store_items = SaleItem.objects.filter(sale__store=store, sale__created_at__range=(m_start, m_end))
            store_agg = store_items.aggregate(s=Sum('total_amount'), c=Sum('total_cost'), p=Sum('profit'))
            s_sales = float(store_agg['s'] or 0)
            s_cost = float(store_agg['c'] or 0)
            s_profit = float(store_agg['p'] or 0)
            
            s_expenses_qs = Expense.objects.filter(store=store, date__year=year, date__month=month)
            s_tot_exp = float(s_expenses_qs.aggregate(t=Sum('amount'))['t'] or 0)
            s_stock_purchases = float(s_expenses_qs.filter(category='PURCHASE').aggregate(t=Sum('amount'))['t'] or 0)
            s_op_exp = s_tot_exp - s_stock_purchases
            s_net_profit = s_profit - s_tot_exp
            
            store_stats.append({
                'name': store.name,
                'sales': s_sales,
                'cost': s_cost,
                'profit': s_profit,
                'operating_expenses': s_op_exp,
                'stock_purchases': s_stock_purchases,
                'net_profit': s_net_profit
            })

        # Top Products
        top_products = list(SaleItem.objects.filter(
            sale__created_at__range=(m_start, m_end)
        ).values('product_name').annotate(
            qty=Sum('quantity'),
            sales=Sum('total_amount'),
            profit=Sum('profit')
        ).order_by('-sales')[:5])

        # Daily sales list
        from django.db.models.functions import TruncDate
        daily_sales = list(Sale.objects.filter(
            created_at__range=(m_start, m_end)
        ).annotate(day=TruncDate('created_at')).values('day', 'store__name').annotate(
            sales=Sum('grand_total'),
            count=Count('id')
        ).order_by('day', 'store__name'))

        # -------------------------------------------------------------
        # Generate Excel Workbook
        # -------------------------------------------------------------
        wb = openpyxl.Workbook()
        
        # Formatting Helpers
        font_family = 'Calibri'
        header_fill = PatternFill(start_color='1A5276', end_color='1A5276', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True, name=font_family, size=11)
        title_font = Font(color='1A5276', bold=True, size=14, name=font_family)
        sub_font = Font(color='555555', bold=True, size=11, name=font_family)
        bold_font = Font(bold=True, name=font_family)
        italic_font = Font(italic=True, name=font_family, size=9)
        thin_border_side = Side(style='thin', color='CCCCCC')
        border_all = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
        center_align = Alignment(horizontal='center', vertical='center')
        right_align = Alignment(horizontal='right')
        left_align = Alignment(horizontal='left')
        alt_row_fill = PatternFill(start_color='EBF5FB', end_color='EBF5FB', fill_type='solid')
        kpi_fill = PatternFill(start_color='D6EAF8', end_color='D6EAF8', fill_type='solid')

        # SHEET 1: Executive Dashboard
        ws1 = wb.active
        ws1.title = "Executive Dashboard"
        
        # Title Block
        ws1.merge_cells('A1:G1')
        ws1['A1'] = f"OCEANWAVES SEA FOODS — Executive Dashboard"
        ws1['A1'].font = title_font
        ws1['A1'].alignment = center_align
        ws1.merge_cells('A2:G2')
        ws1['A2'] = f"Period: {month_name}"
        ws1['A2'].font = sub_font
        ws1['A2'].alignment = center_align

        # Add KPIs
        kpis = [
            ("Total Sales", total_sales, "₹"),
            ("Gross Profit", gross_profit, "₹"),
            ("Stock Purchases", stock_purchases, "₹"),
            ("Operating Expenses", operating_expenses, "₹"),
            ("Net Profit", net_profit, "₹"),
            ("Gross Margin", profit_margin, "%"),
            ("Net Margin", net_margin, "%")
        ]
        
        ws1.cell(row=4, column=1, value="Key Performance Indicators").font = Font(bold=True, size=12, color='1A5276')
        for i, (kpi_lbl, kpi_val, unit) in enumerate(kpis):
            r = 5 + i
            lc = ws1.cell(row=r, column=1, value=kpi_lbl)
            lc.font = bold_font
            lc.fill = kpi_fill
            lc.border = border_all
            
            val_str = f"{unit}{kpi_val:,.2f}" if unit == "₹" else f"{kpi_val:.2f}{unit}"
            vc = ws1.cell(row=r, column=2, value=val_str)
            vc.font = bold_font
            vc.alignment = right_align
            vc.border = border_all

        # Store Performance Table
        ws1.cell(row=14, column=1, value="Store Performance Details").font = Font(bold=True, size=12, color='1A5276')
        headers_store = ["Store Name", "Sales (₹)", "Cost of Goods (₹)", "Gross Profit (₹)", "Operating Exp (₹)", "Stock Purchases (₹)", "Net Profit (₹)"]
        for c_idx, h in enumerate(headers_store, 1):
            cell = ws1.cell(row=15, column=c_idx, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = header_fill, header_font, center_align, border_all
        
        for r_offset, s_stat in enumerate(store_stats):
            r = 16 + r_offset
            row_data = [
                s_stat['name'], s_stat['sales'], s_stat['cost'], s_stat['profit'],
                s_stat['operating_expenses'], s_stat['stock_purchases'], s_stat['net_profit']
            ]
            for c_idx, val in enumerate(row_data, 1):
                cell = ws1.cell(row=r, column=c_idx, value=val)
                cell.border = border_all
                if c_idx > 1:
                    cell.number_format = '₹#,##0.00'
                    cell.alignment = right_align
                else:
                    cell.alignment = left_align
                    cell.font = bold_font

        # Top Selling Products
        ws1.cell(row=14, column=9, value="Top Selling Products").font = Font(bold=True, size=12, color='1A5276')
        headers_prod = ["Product Name", "Qty Sold (kg)", "Sales Amount (₹)", "Profit (₹)"]
        for c_idx, h in enumerate(headers_prod, 9):
            cell = ws1.cell(row=15, column=c_idx, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = header_fill, header_font, center_align, border_all

        for r_offset, p_stat in enumerate(top_products):
            r = 16 + r_offset
            row_data = [
                p_stat['product_name'], float(p_stat['qty']), float(p_stat['sales']), float(p_stat['profit'])
            ]
            for c_offset, val in enumerate(row_data):
                col = 9 + c_offset
                cell = ws1.cell(row=r, column=col, value=val)
                cell.border = border_all
                if c_offset == 0:
                    cell.alignment = left_align
                    cell.font = bold_font
                elif c_offset == 1:
                    cell.number_format = '#,##0.00" kg"'
                    cell.alignment = right_align
                else:
                    cell.number_format = '₹#,##0.00'
                    cell.alignment = right_align

        # Auto-adjust column widths for sheet 1
        for i in range(1, 14):
            col_letter = get_column_letter(i)
            ws1.column_dimensions[col_letter].width = 18 if i != 1 and i != 9 else 24

        # SHEET 2: Daily Sales Breakdown
        ws2 = wb.create_sheet(title="Daily Sales")
        ws2.cell(row=1, column=1, value="Daily Sales Details").font = title_font
        headers_daily = ["Date", "Store Name", "Total Sales Amount (₹)", "Transaction Count"]
        for c_idx, h in enumerate(headers_daily, 1):
            cell = ws2.cell(row=3, column=c_idx, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = header_fill, header_font, center_align, border_all
            
        for idx, row in enumerate(daily_sales, 1):
            r = 3 + idx
            day_str = row['day'].strftime('%d %B %Y') if isinstance(row['day'], (datetime.date, datetime.datetime)) else str(row['day'])
            data = [day_str, row['store__name'], float(row['sales']), row['count']]
            for col_idx, val in enumerate(data, 1):
                cell = ws2.cell(row=r, column=col_idx, value=val)
                cell.border = border_all
                if col_idx == 3:
                    cell.number_format = '₹#,##0.00'
                    cell.alignment = right_align
                elif col_idx == 4:
                    cell.alignment = right_align
                else:
                    cell.alignment = left_align
            if idx % 2 == 0:
                for col_idx in range(1, 5):
                    ws2.cell(row=r, column=col_idx).fill = alt_row_fill

        for i in range(1, 5):
            ws2.column_dimensions[get_column_letter(i)].width = 20

        # SHEET 3: Product Summary
        ws3 = wb.create_sheet(title="Product Summary")
        ws3.cell(row=1, column=1, value="Monthly Product stock & Sales performance").font = title_font
        headers_prod_summary = ["Store", "Product", "Opening Qty (kg)", "Purchased Qty (kg)", "Sold Qty (kg)", "Closing Qty (kg)", "Cost Price (₹)", "Retail Price (₹)", "Sales Amount (₹)", "Profit (₹)"]
        for c_idx, h in enumerate(headers_prod_summary, 1):
            cell = ws3.cell(row=3, column=c_idx, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = header_fill, header_font, center_align, border_all

        # Fetch product lists with snap data
        first_snapshot_date = datetime.date(year, month, 1)
        last_snapshot_date = datetime.date(year, month, last_day)

        sold_list = list(SaleItem.objects.filter(
            sale__created_at__range=(m_start, m_end)
        ).values('sale__store_id', 'sale__store__name', 'product_id', 'product__name', 'sale__bill_type', 'product__cost_price', 'product__retail_price', 'product__stock_quantity').annotate(
            sold_qty=Sum('quantity'),
            total_sale=Sum('total_amount'),
            profit=Sum('profit')
        ))

        purchased_data = list(StockLog.objects.filter(
            store__is_active=True, movement='IN', created_at__range=(m_start, m_end)
        ).values('store_id', 'product_id').annotate(purchased_qty=Sum('quantity')))
        purchased_map = {(row['store_id'], row['product_id']): row['purchased_qty'] for row in purchased_data}

        first_snaps = {(s.store_id, s.product_id): s.opening_qty for s in DailyStockSnapshot.objects.filter(date=first_snapshot_date)}
        last_snaps = {(s.store_id, s.product_id): s.closing_qty for s in DailyStockSnapshot.objects.filter(date=last_snapshot_date)}

        row_idx = 4
        displayed_products = set()
        for s_idx, row in enumerate(sold_list):
            sid = row['sale__store_id']
            pid = row['product_id']
            sname = row['sale__store__name']
            pname = row['product__name']
            bill_type = row['sale__bill_type']
            sold_qty = float(row['sold_qty'] or 0)
            total_sale = float(row['total_sale'] or 0)
            profit_val = float(row['profit'] or 0)
            purchased_qty = float(purchased_map.get((sid, pid), 0))
            
            bill_type_label = "Retail" if bill_type == 'RETAIL' else "Wholesale"
            
            if (sid, pid) not in displayed_products:
                opening_qty = float(first_snaps.get((sid, pid), 0))
                closing_qty = float(last_snaps.get((sid, pid), 0))
                if not closing_qty:
                    closing_qty = float(row['product__stock_quantity'] or 0)
                displayed_products.add((sid, pid))
            else:
                opening_qty = 0
                closing_qty = 0
                purchased_qty = 0
            
            cost_price = float(row['product__cost_price'] or 0)
            sell_price = float(row['product__retail_price'] or 0)

            data = [
                sname, f"{pname} ({bill_type_label})", opening_qty, purchased_qty, sold_qty, closing_qty,
                cost_price, sell_price, total_sale, profit_val
            ]
            for col_idx, val in enumerate(data, 1):
                cell = ws3.cell(row=row_idx, column=col_idx, value=val)
                cell.border = border_all
                if col_idx > 8:
                    cell.number_format = '₹#,##0.00'
                    cell.alignment = right_align
                elif col_idx in [3, 4, 5, 6]:
                    cell.number_format = '#,##0.00" kg"'
                    cell.alignment = right_align
                elif col_idx in [7, 8]:
                    cell.number_format = '₹#,##0.00'
                    cell.alignment = right_align
                else:
                    cell.alignment = left_align
            if s_idx % 2 == 0:
                for col_idx in range(1, 11):
                    ws3.cell(row=row_idx, column=col_idx).fill = alt_row_fill
            row_idx += 1

        for i in range(1, 11):
            ws3.column_dimensions[get_column_letter(i)].width = 16 if i not in [1, 2] else 22

        # SHEET 4: Detailed Expenses
        ws4 = wb.create_sheet(title="Expenses & Purchases")
        ws4.cell(row=1, column=1, value="Monthly Expenses & Purchases Details").font = title_font
        headers_exp = ["Date", "Store Name", "Category", "Type", "Description", "Amount (₹)", "Status"]
        for c_idx, h in enumerate(headers_exp, 1):
            cell = ws4.cell(row=3, column=c_idx, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = header_fill, header_font, center_align, border_all

        for idx, exp in enumerate(expenses_qs.select_related('store').order_by('date', 'store__name'), 1):
            r = 3 + idx
            data = [
                str(exp.date), exp.store.name, exp.get_category_display(),
                exp.get_expense_type_display(), exp.description, float(exp.amount),
                exp.get_status_display()
            ]
            for col_idx, val in enumerate(data, 1):
                cell = ws4.cell(row=r, column=col_idx, value=val)
                cell.border = border_all
                if col_idx == 6:
                    cell.number_format = '₹#,##0.00'
                    cell.alignment = right_align
                elif col_idx == 7:
                    cell.alignment = center_align
                    cell.font = bold_font
                    if val == "Paid":
                        cell.font = Font(color="03543F", bold=True)
                    else:
                        cell.font = Font(color="9B1C1C", bold=True)
                else:
                    cell.alignment = left_align
            if idx % 2 == 0:
                for col_idx in range(1, 8):
                    ws4.cell(row=r, column=col_idx).fill = alt_row_fill

        for i in range(1, 8):
            ws4.column_dimensions[get_column_letter(i)].width = 18 if i != 5 else 32

        # Save workbook to memory buffer
        excel_file = io.BytesIO()
        wb.save(excel_file)
        excel_file.seek(0)

        # -------------------------------------------------------------
        # Generate HTML Email content
        # -------------------------------------------------------------
        store_rows_html = ""
        for s_stat in store_stats:
            store_rows_html += f"""
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 10px; text-align: left; font-weight: bold; color: #334155;">{s_stat['name']}</td>
                <td style="padding: 10px; text-align: right;">₹{s_stat['sales']:,.2f}</td>
                <td style="padding: 10px; text-align: right; color: #64748b;">₹{s_stat['cost']:,.2f}</td>
                <td style="padding: 10px; text-align: right; color: #16a34a; font-weight: 600;">₹{s_stat['profit']:,.2f}</td>
                <td style="padding: 10px; text-align: right; color: #e11d48;">₹{s_stat['operating_expenses']:,.2f}</td>
                <td style="padding: 10px; text-align: right; color: #d97706;">₹{s_stat['stock_purchases']:,.2f}</td>
                <td style="padding: 10px; text-align: right; font-weight: bold; color: {'#16a34a' if s_stat['net_profit'] >= 0 else '#dc2626'};">₹{s_stat['net_profit']:,.2f}</td>
            </tr>
            """

        top_prod_rows_html = ""
        for idx, p_stat in enumerate(top_products, 1):
            top_prod_rows_html += f"""
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 8px; text-align: center; color: #64748b;">{idx}</td>
                <td style="padding: 8px; text-align: left; font-weight: 600; color: #1e293b;">{p_stat['product_name']}</td>
                <td style="padding: 8px; text-align: right;">{p_stat['qty']:,.2f} kg</td>
                <td style="padding: 8px; text-align: right; font-weight: 600; color: #0f172a;">₹{p_stat['sales']:,.2f}</td>
            </tr>
            """

        html_content = f"""
        <html>
        <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 20px;">
            <div style="max-width: 800px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); border: 1px solid #e2e8f0;">
                
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #1e3a8a 0%, #0d9488 100%); padding: 30px; text-align: center; color: #ffffff;">
                    <h1 style="margin: 0; font-size: 24px; font-weight: 850; letter-spacing: 0.5px;">OCEANWAVES SEA FOODS</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px; font-weight: 500;">Consolidated Monthly Business Report — {month_name}</p>
                </div>

                <!-- Body wrapper -->
                <div style="padding: 30px;">
                    
                    <p style="font-size: 15px; line-height: 1.5; color: #475569; margin-top: 0;">
                        Hello Admin,<br><br>
                        Please find below the consolidated business report and KPIs for the month of <strong>{month_name}</strong>. A detailed itemized report is attached as an Excel sheet for your review.
                    </p>

                    <!-- KPI Cards Grid -->
                    <h3 style="color: #1e3a8a; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; margin: 25px 0 15px 0; font-size: 16px;">Consolidated KPIs</h3>
                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; margin-bottom: 30px;">
                        
                        <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 15px; text-align: left;">
                            <span style="font-size: 11px; text-transform: uppercase; color: #166534; font-weight: 700; letter-spacing: 0.5px;">Total Sales</span>
                            <div style="font-size: 22px; font-weight: 800; color: #166534; margin-top: 5px;">₹{total_sales:,.2f}</div>
                        </div>

                        <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 15px; text-align: left;">
                            <span style="font-size: 11px; text-transform: uppercase; color: #166534; font-weight: 700; letter-spacing: 0.5px;">Gross Profit</span>
                            <div style="font-size: 22px; font-weight: 800; color: #166534; margin-top: 5px;">₹{gross_profit:,.2f}</div>
                        </div>

                        <div style="background-color: #fef3c7; border: 1px solid #fde68a; border-radius: 8px; padding: 15px; text-align: left;">
                            <span style="font-size: 11px; text-transform: uppercase; color: #d97706; font-weight: 700; letter-spacing: 0.5px;">Stock Purchases</span>
                            <div style="font-size: 22px; font-weight: 800; color: #b45309; margin-top: 5px;">₹{stock_purchases:,.2f}</div>
                        </div>

                        <div style="background-color: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 15px; text-align: left;">
                            <span style="font-size: 11px; text-transform: uppercase; color: #991b1b; font-weight: 700; letter-spacing: 0.5px;">Operating Expenses</span>
                            <div style="font-size: 22px; font-weight: 800; color: #991b1b; margin-top: 5px;">₹{operating_expenses:,.2f}</div>
                        </div>

                        <div style="background-color: {'#ecfdf5' if net_profit >= 0 else '#fff5f5'}; border: 1px solid {'#a7f3d0' if net_profit >= 0 else '#fecaca'}; border-radius: 8px; padding: 15px; text-align: left; grid-column: span 2;">
                            <span style="font-size: 11px; text-transform: uppercase; color: {'#065f46' if net_profit >= 0 else '#991b1b'}; font-weight: 700; letter-spacing: 0.5px;">Net Business Profit</span>
                            <div style="font-size: 26px; font-weight: 800; color: {'#065f46' if net_profit >= 0 else '#991b1b'}; margin-top: 5px;">₹{net_profit:,.2f}</div>
                            <span style="font-size: 12px; color: #64748b; margin-top: 4px; display: block;">Net Profit Margin: <strong>{net_margin:.2f}%</strong> | Gross Profit Margin: <strong>{profit_margin:.2f}%</strong></span>
                        </div>

                    </div>

                    <!-- Store Breakdown Table -->
                    <h3 style="color: #1e3a8a; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; margin: 25px 0 15px 0; font-size: 16px;">Store Breakdown</h3>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px; font-size: 13px;">
                        <thead>
                            <tr style="background-color: #f1f5f9; border-bottom: 2px solid #cbd5e1; color: #475569; font-weight: bold;">
                                <th style="padding: 10px; text-align: left;">Store</th>
                                <th style="padding: 10px; text-align: right;">Sales</th>
                                <th style="padding: 10px; text-align: right;">Cost</th>
                                <th style="padding: 10px; text-align: right;">Gross Profit</th>
                                <th style="padding: 10px; text-align: right;">Op. Exp</th>
                                <th style="padding: 10px; text-align: right;">Purchases</th>
                                <th style="padding: 10px; text-align: right;">Net Profit</th>
                            </tr>
                        </thead>
                        <tbody>
                            {store_rows_html}
                        </tbody>
                    </table>

                    <!-- Top Selling Products Table -->
                    <h3 style="color: #1e3a8a; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; margin: 25px 0 15px 0; font-size: 16px;">Top 5 Selling Products</h3>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px; font-size: 13px;">
                        <thead>
                            <tr style="background-color: #f1f5f9; border-bottom: 2px solid #cbd5e1; color: #475569; font-weight: bold;">
                                <th style="padding: 8px; text-align: center; width: 40px;">Rank</th>
                                <th style="padding: 8px; text-align: left;">Product</th>
                                <th style="padding: 8px; text-align: right;">Quantity</th>
                                <th style="padding: 8px; text-align: right;">Revenue</th>
                            </tr>
                        </thead>
                        <tbody>
                            {top_prod_rows_html}
                        </tbody>
                    </table>

                    <p style="font-size: 13px; color: #64748b; font-style: italic; border-top: 1px solid #e2e8f0; padding-top: 15px; margin-top: 30px;">
                        Note: This report is auto-generated by the OceanWaves POS system. Please review the attached Microsoft Excel spreadsheet for complete daily details, product inventory snapshots, and individual operating expenses.
                    </p>

                </div>

                <!-- Footer -->
                <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 20px; text-align: center; font-size: 12px; color: #64748b;">
                    OCEANWAVES SEA FOODS is part of OCEANWAVES VICTUALS PRIVATE LIMITED.<br>
                    &copy; 2026 OceanWaves POS System. All rights reserved.
                </div>

            </div>
        </body>
        </html>
        """

        # -------------------------------------------------------------
        # Determine Email Recipients
        # -------------------------------------------------------------
        recipients = []
        admin_emails_str = getattr(settings, 'ADMIN_EMAILS', '') or os.environ.get('ADMIN_EMAILS', '')
        if admin_emails_str:
            recipients = [e.strip() for e in admin_emails_str.split(',') if e.strip()]
        
        if not recipients:
            # Fall back to SUPERADMIN profiles
            recipients = list(User.objects.filter(
                profile__role='SUPERADMIN',
                email__isnull=False
            ).exclude(email='').values_list('email', flat=True))

        if not recipients:
            # Fall back to OWNER profiles
            recipients = list(User.objects.filter(
                profile__role='OWNER',
                email__isnull=False
            ).exclude(email='').values_list('email', flat=True))

        if not recipients:
            self.stderr.write("Error: No admin emails configured or found in the database. Report could not be sent.")
            return

        self.stdout.write(f"Sending email to: {recipients}")

        # Send Email
        try:
            subject = f"OceanWaves Monthly Business Report — {month_name}"
            email = EmailMessage(
                subject=subject,
                body=html_content,
                from_email=settings.DEFAULT_FROM_EMAIL or 'no-reply@oceanwavessfd.com',
                to=recipients,
            )
            email.content_subtype = "html"
            email.attach(
                filename=f"OceanWaves_Monthly_Report_{year}_{month:02d}.xlsx",
                content=excel_file.getvalue(),
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            email.send(fail_silently=False)
            self.stdout.write(self.style.SUCCESS(f"Successfully generated and emailed monthly report for {month_name} to {recipients}"))
        except Exception as e:
            self.stderr.write(f"Failed to send monthly report email: {e}")
            raise e

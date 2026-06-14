import datetime
import calendar
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.db.models import Sum, Q
from django.utils.timezone import make_aware, get_current_timezone
from .models import SaleItem, Expense, DailyStockSnapshot, Store, Product, StockLog
from .views import get_profile, require_profile
from django.core.cache import cache


def _get_report_store(request, profile):
    """Return the store to report on; superadmin can pick via ?store_id="""
    if profile.is_superadmin:
        store_id = request.GET.get('store_id') or request.POST.get('store_id')
        if store_id:
            return get_object_or_404(Store, id=store_id)
        # Default to first active store
        return Store.objects.filter(is_active=True).first()
    return profile.store


def _date_range(d):
    tz    = get_current_timezone()
    start = make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
    end   = make_aware(datetime.datetime.combine(d, datetime.time.max), tz)
    return start, end


def _month_range(year, month):
    tz          = get_current_timezone()
    start       = make_aware(datetime.datetime(year, month, 1, 0, 0, 0), tz)
    last_day    = calendar.monthrange(year, month)[1]
    end         = make_aware(datetime.datetime(year, month, last_day, 23, 59, 59), tz)
    return start, end


# ══════════════════════════════════════════════════════════════════════════════
#  DAILY REPORT
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def daily_report_view(request):
    profile = get_profile(request.user)
    store   = _get_report_store(request, profile)

    if not store:
        messages.error(request, 'No store found.')
        return redirect('dashboard')

    if not (profile.is_superadmin or profile.is_owner or profile.is_subadmin):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    from_date_str = request.GET.get('from_date', '')
    to_date_str = request.GET.get('to_date', '')
    try:
        from_date = datetime.date.fromisoformat(from_date_str) if from_date_str else datetime.date.today()
    except ValueError:
        from_date = datetime.date.today()
        
    try:
        to_date = datetime.date.fromisoformat(to_date_str) if to_date_str else from_date
    except ValueError:
        to_date = from_date

    r_start = _date_range(from_date)[0]
    r_end = _date_range(to_date)[1]

    if True:
        # ── Product-level data ──────────────────────────────────────────────────
        # Sold quantities per product today
        sold_list = list(SaleItem.objects
            .filter(sale__store=store, sale__created_at__range=(r_start, r_end))
            .values('product_id', 'product__name', 'sale__bill_type', 'selling_price')
            .annotate(
                sold_qty   = Sum('quantity'),
                total_sale = Sum('total_amount'),
                profit     = Sum('profit'),
                total_cost = Sum('total_cost'),
            ))
        sold_map = {}
        for row in sold_list:
            sold_map.setdefault(row['product_id'], []).append(row)

        # Purchased (stock-in) quantities per product today
        purchased_list = list(StockLog.objects
            .filter(store=store, movement='IN', created_at__range=(r_start, r_end))
            .values('product_id')
            .annotate(purchased_qty=Sum('quantity')))
        purchased_map = {row['product_id']: row['purchased_qty'] for row in purchased_list}

        # DailyStockSnapshot for opening (first day) and closing (last day)
        first_snaps = {s.product_id: s for s in DailyStockSnapshot.objects.filter(store=store, date=from_date).select_related('product')}
        last_snaps = {s.product_id: s for s in DailyStockSnapshot.objects.filter(store=store, date=to_date).select_related('product')}

        product_rows = []
        total_sold_rows = []
        retail_product_rows = []
        wholesale_product_rows = []

        # Get active products of the store
        active_products = Product.objects.filter(store=store, is_active=True)
        # Get any other products that had transactions/snapshots in this store
        relevant_product_ids = set(active_products.values_list('id', flat=True))
        relevant_product_ids.update(sold_map.keys())
        relevant_product_ids.update(purchased_map.keys())
        relevant_product_ids.update(first_snaps.keys())
        relevant_product_ids.update(last_snaps.keys())

        products = list(Product.objects.filter(id__in=relevant_product_ids).order_by('category', 'name'))
        for p in products:
            solds = sold_map.get(p.id, [])
            purchased_qty = float(purchased_map.get(p.id, 0))
            
            if p.id in first_snaps:
                opening_qty_prod = float(first_snaps[p.id].opening_qty or 0)
            else:
                total_sold_for_prod = sum(float(x['sold_qty']) for x in solds)
                opening_qty_prod = float(p.stock_quantity) + total_sold_for_prod - purchased_qty
                
            if p.id in last_snaps:
                closing_qty_prod = float(last_snaps[p.id].closing_qty or 0)
            else:
                closing_qty_prod = float(p.stock_quantity)
                
            # 1. Total Sold
            total_sold_qty = sum(float(x['sold_qty']) for x in solds)
            total_sold_rows.append({
                'product_name': p.name,
                'opening_qty': opening_qty_prod,
                'purchased_qty': purchased_qty,
                'sold_qty': total_sold_qty,
                'closing_qty': closing_qty_prod,
            })

            # 2. Retail Product rows
            retail_solds = [x for x in solds if x['sale__bill_type'] == 'RETAIL']
            if not retail_solds:
                retail_product_rows.append({
                    'product_name'  : p.name,
                    'opening_qty'   : opening_qty_prod,
                    'purchased_qty' : purchased_qty,
                    'purchase_price': float(p.cost_price or 0),
                    'sold_qty'      : 0,
                    'selling_price' : float(p.retail_price or 0),
                    'closing_qty'   : closing_qty_prod,
                    'profit'        : 0,
                    'total_sale'    : 0,
                })
            else:
                for idx, sold in enumerate(retail_solds):
                    sold_qty      = float(sold['sold_qty'])
                    total_sale    = float(sold['total_sale'])
                    profit_val    = float(sold['profit'])
                    retail_product_rows.append({
                        'product_name'  : p.name if len(retail_solds) == 1 else f"{p.name} (price {sold['selling_price']})",
                        'opening_qty'   : opening_qty_prod if idx == 0 else 0,
                        'purchased_qty' : purchased_qty if idx == 0 else 0,
                        'purchase_price': float(p.cost_price or 0) if idx == 0 else 0,
                        'sold_qty'      : sold_qty,
                        'selling_price' : float(sold['selling_price']),
                        'closing_qty'   : closing_qty_prod if idx == 0 else 0,
                        'profit'        : profit_val,
                        'total_sale'    : total_sale,
                    })

            # 3. Wholesale Product rows
            wholesale_solds = [x for x in solds if x['sale__bill_type'] == 'WHOLESALE']
            if not wholesale_solds:
                wholesale_product_rows.append({
                    'product_name'  : p.name,
                    'opening_qty'   : opening_qty_prod,
                    'purchased_qty' : purchased_qty,
                    'purchase_price': float(p.cost_price or 0),
                    'sold_qty'      : 0,
                    'selling_price' : float(p.wholesale_price or 0),
                    'closing_qty'   : closing_qty_prod,
                    'profit'        : 0,
                    'total_sale'    : 0,
                })
            else:
                for idx, sold in enumerate(wholesale_solds):
                    sold_qty      = float(sold['sold_qty'])
                    total_sale    = float(sold['total_sale'])
                    profit_val    = float(sold['profit'])
                    wholesale_product_rows.append({
                        'product_name'  : p.name if len(wholesale_solds) == 1 else f"{p.name} (price {sold['selling_price']})",
                        'opening_qty'   : opening_qty_prod if idx == 0 else 0,
                        'purchased_qty' : purchased_qty if idx == 0 else 0,
                        'purchase_price': float(p.cost_price or 0) if idx == 0 else 0,
                        'sold_qty'      : sold_qty,
                        'selling_price' : float(sold['selling_price']),
                        'closing_qty'   : closing_qty_prod if idx == 0 else 0,
                        'profit'        : profit_val,
                        'total_sale'    : total_sale,
                    })

            # Keep original product_rows for compatibility
            if not solds:
                product_rows.append({
                    'product_name'  : p.name,
                    'opening_qty'   : opening_qty_prod,
                    'purchased_qty' : purchased_qty,
                    'purchase_price': float(p.cost_price or 0),
                    'sold_qty'      : 0,
                    'selling_price' : float(p.retail_price or 0),
                    'closing_qty'   : closing_qty_prod,
                    'profit'        : 0,
                    'total_sale'    : 0,
                })
            else:
                for idx, sold in enumerate(solds):
                    sold_qty      = float(sold['sold_qty'])
                    total_sale    = float(sold['total_sale'])
                    profit_val    = float(sold['profit'])
                    bill_type_label = "Retail" if sold['sale__bill_type'] == 'RETAIL' else "Wholesale"
                    product_rows.append({
                        'product_name'  : f"{p.name} ({bill_type_label})",
                        'opening_qty'   : opening_qty_prod if idx == 0 else 0,
                        'purchased_qty' : purchased_qty if idx == 0 else 0,
                        'purchase_price': float(p.cost_price or 0) if idx == 0 else 0,
                        'sold_qty'      : sold_qty,
                        'selling_price' : float(sold['selling_price']),
                        'closing_qty'   : closing_qty_prod if idx == 0 else 0,
                        'profit'        : profit_val,
                        'total_sale'    : total_sale,
                    })

        # Totals for total_sold
        total_sold_totals = {
            'opening_qty': sum(row['opening_qty'] for row in total_sold_rows),
            'purchased_qty': sum(row['purchased_qty'] for row in total_sold_rows),
            'sold_qty': sum(row['sold_qty'] for row in total_sold_rows),
            'closing_qty': sum(row['closing_qty'] for row in total_sold_rows),
        }

        # Totals for retail
        retail_totals = {
            'opening_qty': sum(row['opening_qty'] for row in retail_product_rows),
            'purchased_qty': sum(row['purchased_qty'] for row in retail_product_rows),
            'sold_qty': sum(row['sold_qty'] for row in retail_product_rows),
            'closing_qty': sum(row['closing_qty'] for row in retail_product_rows),
            'profit': sum(row['profit'] for row in retail_product_rows),
            'total_sale': sum(row['total_sale'] for row in retail_product_rows),
        }

        # Totals for wholesale
        wholesale_totals = {
            'opening_qty': sum(row['opening_qty'] for row in wholesale_product_rows),
            'purchased_qty': sum(row['purchased_qty'] for row in wholesale_product_rows),
            'sold_qty': sum(row['sold_qty'] for row in wholesale_product_rows),
            'closing_qty': sum(row['closing_qty'] for row in wholesale_product_rows),
            'profit': sum(row['profit'] for row in wholesale_product_rows),
            'total_sale': sum(row['total_sale'] for row in wholesale_product_rows),
        }

        # Also get total retail sales and total wholesale sales amounts directly
        retail_sales_amount = float(SaleItem.objects.filter(
            sale__store=store, sale__created_at__range=(r_start, r_end), sale__bill_type='RETAIL'
        ).aggregate(s=Sum('total_amount'))['s'] or 0)

        wholesale_sales_amount = float(SaleItem.objects.filter(
            sale__store=store, sale__created_at__range=(r_start, r_end), sale__bill_type='WHOLESALE'
        ).aggregate(s=Sum('total_amount'))['s'] or 0)

        # ── Summary ─────────────────────────────────────────────────────────────
        agg = SaleItem.objects.filter(
            sale__store=store, sale__created_at__range=(r_start, r_end)
        ).aggregate(sales=Sum('total_amount'), cost=Sum('total_cost'), profit=Sum('profit'))
        total_sales   = float(agg['sales']  or 0)
        gross_profit  = float(agg['profit'] or 0)

        total_expenses = float(Expense.objects.filter(store=store, date__range=(from_date, to_date)).exclude(category='PURCHASE').aggregate(t=Sum('amount'))['t'] or 0)

        net_profit        = gross_profit - total_expenses
        profit_percentage = float(net_profit / total_sales * 100) if total_sales else 0

    # Re-fetch specific expense lists (cheap)
    daily_expenses   = Expense.objects.filter(store=store, date__range=(from_date, to_date), expense_type='DAILY').exclude(category='PURCHASE')
    daily_expenses_sum = float(daily_expenses.aggregate(t=Sum('amount'))['t'] or 0)
    monthly_expenses = Expense.objects.filter(store=store, date__range=(from_date, to_date), expense_type='MONTHLY').exclude(category='PURCHASE')
    all_expenses     = Expense.objects.filter(store=store, date__range=(from_date, to_date)).exclude(category='PURCHASE')

    all_stores = Store.objects.filter(is_active=True) if profile.is_superadmin else None

    return render(request, 'pos/reports_daily.html', {
        'profile'          : profile,
        'store'            : store,
        'all_stores'       : all_stores,
        'from_date'        : from_date,
        'to_date'          : to_date,
        'product_rows'     : product_rows,
        'total_sold_rows'  : total_sold_rows,
        'retail_product_rows': retail_product_rows,
        'wholesale_product_rows': wholesale_product_rows,
        'total_sold_totals': total_sold_totals,
        'retail_totals'    : retail_totals,
        'wholesale_totals' : wholesale_totals,
        'retail_sales_amount': retail_sales_amount,
        'wholesale_sales_amount': wholesale_sales_amount,
        'daily_expenses'   : daily_expenses,
        'daily_expenses_sum': daily_expenses_sum,
        'monthly_expenses' : monthly_expenses,
        'all_expenses'     : all_expenses,
        'total_sales'      : total_sales,
        'gross_profit'     : gross_profit,
        'total_expenses'   : total_expenses,
        'net_profit'       : net_profit,
        'profit_percentage': profit_percentage,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  MONTHLY REPORT
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def monthly_report_view(request):
    profile = get_profile(request.user)
    store   = _get_report_store(request, profile)

    if not store:
        messages.error(request, 'No store found.')
        return redirect('dashboard')

    if not (profile.is_superadmin or profile.is_owner or profile.is_subadmin):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    month_str = request.GET.get('month', datetime.date.today().strftime('%Y-%m'))
    try:
        year, month = map(int, month_str.split('-'))
    except ValueError:
        year, month = datetime.date.today().year, datetime.date.today().month

    m_start, m_end = _month_range(year, month)

    cache_key = f'pos_monthly_report_{store.id}_{year}_{month}'
    cached_data = None
    try:
        cached_data = cache.get(cache_key)
    except Exception:
        pass

    if not cached_data:
        # ── Product-level aggregated sales ──────────────────────────────────────
        sold_list = list(SaleItem.objects
            .filter(sale__store=store, sale__created_at__range=(m_start, m_end))
            .values('product_id', 'product__name', 'sale__bill_type', 'product__cost_price', 'product__retail_price')
            .annotate(
                sold_qty   = Sum('quantity'),
                total_sale = Sum('total_amount'),
                profit     = Sum('profit'),
                total_cost = Sum('total_cost'),
            ))

        # Purchased in the month
        purchased_list = list(StockLog.objects
            .filter(store=store, movement='IN', created_at__range=(m_start, m_end))
            .values('product_id')
            .annotate(purchased_qty=Sum('quantity')))
        purchased_map = {row['product_id']: row['purchased_qty'] for row in purchased_list}

        # Snapshots: get first (opening) and last (closing) snapshot per product
        first_snapshot_date = datetime.date(year, month, 1)
        last_snapshot_date  = datetime.date(year, month, calendar.monthrange(year, month)[1])

        first_snaps = {
            s.product_id: s for s in DailyStockSnapshot.objects.filter(
                store=store, date=first_snapshot_date
            )
        }
        last_snaps = {
            s.product_id: s for s in DailyStockSnapshot.objects.filter(
                store=store, date=last_snapshot_date
            )
        }

        product_rows = []
        displayed_products = set()
        for row in sold_list:
            pid           = row['product_id']
            pname         = row['product__name']
            bill_type     = row['sale__bill_type']
            sold_qty      = float(row['sold_qty'] or 0)
            total_sale    = float(row['total_sale'] or 0)
            profit_val    = float(row['profit'] or 0)
            
            bill_type_label = "Retail" if bill_type == 'RETAIL' else "Wholesale"
            
            if pid not in displayed_products:
                opening_qty   = float(first_snaps[pid].opening_qty if pid in first_snaps else 0)
                closing_qty   = float(last_snaps[pid].closing_qty  if pid in last_snaps  else 0)
                purchased_qty = float(purchased_map.get(pid, 0))
                displayed_products.add(pid)
            else:
                opening_qty   = 0
                closing_qty   = 0
                purchased_qty = 0

            cost_price    = float(row['product__cost_price'] or 0)
            selling_price = float(row['product__retail_price'] or 0)

            product_rows.append({
                'product_name'  : f"{pname} ({bill_type_label})",
                'opening_qty'   : opening_qty,
                'purchased_qty' : purchased_qty,
                'purchase_price': cost_price if purchased_qty > 0 or opening_qty > 0 else 0,
                'sold_qty'      : sold_qty,
                'selling_price' : selling_price,
                'closing_qty'   : closing_qty,
                'profit'        : profit_val,
                'total_sale'    : total_sale,
            })

        # ── Summary ─────────────────────────────────────────────────────────────
        agg = SaleItem.objects.filter(
            sale__store=store, sale__created_at__range=(m_start, m_end)
        ).aggregate(sales=Sum('total_amount'), cost=Sum('total_cost'), profit=Sum('profit'))

        total_sales  = float(agg['sales']  or 0)
        gross_profit = float(agg['profit'] or 0)

        total_expenses = float(Expense.objects.filter(store=store, date__year=year, date__month=month).aggregate(t=Sum('amount'))['t'] or 0)

        net_profit        = gross_profit - total_expenses
        profit_percentage = float(net_profit / total_sales * 100) if total_sales else 0
        
        cached_data = {
            'product_rows'     : product_rows,
            'total_sales'      : total_sales,
            'gross_profit'     : gross_profit,
            'total_expenses'   : total_expenses,
            'net_profit'       : net_profit,
            'profit_percentage': profit_percentage,
        }
        try:
            _today = datetime.date.today()
            _is_history = (year < _today.year) or (year == _today.year and month < _today.month)
            timeout = 3600 if _is_history else 60
            cache.set(cache_key, cached_data, timeout=timeout)
        except Exception:
            pass

    # Re-fetch specific expense lists/totals (cheap)
    daily_exp   = Expense.objects.filter(store=store, date__year=year, date__month=month, expense_type='DAILY')
    monthly_exp = Expense.objects.filter(store=store, date__year=year, date__month=month, expense_type='MONTHLY')
    daily_exp_total   = daily_exp.aggregate(t=Sum('amount'))['t']   or 0
    monthly_exp_total = monthly_exp.aggregate(t=Sum('amount'))['t'] or 0

    all_stores = Store.objects.filter(is_active=True) if profile.is_superadmin else None

    return render(request, 'pos/reports_monthly.html', {
        'profile'           : profile,
        'store'             : store,
        'all_stores'        : all_stores,
        'month_str'         : month_str,
        'year'              : year,
        'month'             : month,
        'month_name'        : datetime.date(year, month, 1).strftime('%B %Y'),
        'product_rows'      : cached_data['product_rows'],
        'daily_expenses'    : daily_exp,
        'monthly_expenses'  : monthly_exp,
        'daily_exp_total'   : daily_exp_total,
        'monthly_exp_total' : monthly_exp_total,
        'total_sales'       : cached_data['total_sales'],
        'gross_profit'      : cached_data['gross_profit'],
        'total_expenses'    : cached_data['total_expenses'],
        'net_profit'        : cached_data['net_profit'],
        'profit_percentage' : cached_data['profit_percentage'],
    })


# ══════════════════════════════════════════════════════════════════════════════
#  EXCEL EXPORTS
# ══════════════════════════════════════════════════════════════════════════════
def _style_header(ws, row_num, headers, fill_color='1A5276'):
    """Apply header styling to an openpyxl worksheet row."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    fill   = PatternFill(start_color=fill_color, end_color=fill_color, fill_type='solid')
    font   = Font(color='FFFFFF', bold=True, name='Calibri', size=11)
    border = Border(
        bottom=Side(style='medium', color='FFFFFF'),
        right =Side(style='thin',   color='FFFFFF'),
    )
    align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=row_num, column=col, value=header)
        cell.fill   = fill
        cell.font   = font
        cell.border = border
        cell.alignment = align


def _style_summary_row(ws, row_num, label_col, value_col, label, value, label_color='1A5276'):
    from openpyxl.styles import Font, PatternFill, Alignment
    lc = ws.cell(row=row_num, column=label_col, value=label)
    lc.font      = Font(bold=True, color='FFFFFF', name='Calibri')
    lc.fill      = PatternFill(start_color=label_color, end_color=label_color, fill_type='solid')
    lc.alignment = Alignment(horizontal='right')
    vc = ws.cell(row=row_num, column=value_col, value=float(value) if value else 0)
    vc.font      = Font(bold=True, name='Calibri')
    vc.alignment = Alignment(horizontal='right')


@login_required
@require_profile
def export_daily_excel(request):
    """Export Daily Report to Excel."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, numbers
    from openpyxl.utils import get_column_letter

    profile = get_profile(request.user)
    store   = _get_report_store(request, profile)
    if not store or not (profile.is_superadmin or profile.is_owner or profile.is_subadmin):
        return redirect('dashboard')

    from_date_str = request.GET.get('from_date', '')
    to_date_str = request.GET.get('to_date', '')
    try:
        from_date = datetime.date.fromisoformat(from_date_str) if from_date_str else datetime.date.today()
    except ValueError:
        from_date = datetime.date.today()
        
    try:
        to_date = datetime.date.fromisoformat(to_date_str) if to_date_str else from_date
    except ValueError:
        to_date = from_date

    r_start = _date_range(from_date)[0]
    r_end = _date_range(to_date)[1]

    sold_data = (
        SaleItem.objects.filter(sale__store=store, sale__created_at__range=(r_start, r_end))
        .values('product_id', 'product__name', 'sale__bill_type', 'selling_price')
        .annotate(sold_qty=Sum('quantity'), total_sale=Sum('total_amount'), profit=Sum('profit'))
    )
    sold_map = {}
    for row in sold_data:
        sold_map.setdefault(row['product_id'], []).append(row)

    purchased_data = (
        StockLog.objects.filter(store=store, movement='IN', created_at__range=(r_start, r_end))
        .values('product_id').annotate(purchased_qty=Sum('quantity'))
    )
    purchased_map = {row['product_id']: row['purchased_qty'] for row in purchased_data}

    first_snaps = {s.product_id: s for s in DailyStockSnapshot.objects.filter(store=store, date=from_date).select_related('product')}
    last_snaps = {s.product_id: s for s in DailyStockSnapshot.objects.filter(store=store, date=to_date).select_related('product')}

    # Get active products of the store
    active_products = Product.objects.filter(store=store, is_active=True)
    # Get any other products that had transactions/snapshots in this store
    relevant_product_ids = set(active_products.values_list('id', flat=True))
    relevant_product_ids.update(sold_map.keys())
    relevant_product_ids.update(purchased_map.keys())
    relevant_product_ids.update(first_snaps.keys())
    relevant_product_ids.update(last_snaps.keys())

    product_rows = []
    products = Product.objects.filter(id__in=relevant_product_ids).order_by('category', 'name')
    for p in products:
        solds = sold_map.get(p.id, [])
        purchased_qty = float(purchased_map.get(p.id, 0))
        
        if p.id in first_snaps:
            opening_qty_prod = float(first_snaps[p.id].opening_qty or 0)
        else:
            total_sold_for_prod = sum(float(x['sold_qty']) for x in solds)
            opening_qty_prod = float(p.stock_quantity) + total_sold_for_prod - purchased_qty
            
        if p.id in last_snaps:
            closing_qty_prod = float(last_snaps[p.id].closing_qty or 0)
        else:
            closing_qty_prod = float(p.stock_quantity)
            
        if not solds:
            product_rows.append({
                'name'         : p.name,
                'opening_qty'  : opening_qty_prod,
                'purchased_qty': purchased_qty,
                'cost_price'   : float(p.cost_price or 0),
                'sold_qty'     : 0,
                'sell_price'   : float(p.retail_price or 0),
                'closing_qty'  : closing_qty_prod,
                'profit'       : 0,
                'total_sale'   : 0,
            })
        else:
            for idx, sold in enumerate(solds):
                sold_qty      = float(sold['sold_qty'])
                total_sale    = float(sold['total_sale'])
                profit_val    = float(sold['profit'])
                
                bill_type_label = "Retail" if sold['sale__bill_type'] == 'RETAIL' else "Wholesale"
                
                product_rows.append({
                    'name'         : f"{p.name} ({bill_type_label})",
                    'opening_qty'  : opening_qty_prod if idx == 0 else 0,
                    'purchased_qty': purchased_qty if idx == 0 else 0,
                    'cost_price'   : float(p.cost_price or 0) if idx == 0 else 0,
                    'sold_qty'     : sold_qty,
                    'sell_price'   : float(sold['selling_price']),
                    'closing_qty'  : closing_qty_prod if idx == 0 else 0,
                    'profit'       : profit_val,
                    'total_sale'   : total_sale,
                })

    expenses = Expense.objects.filter(store=store, date__range=(from_date, to_date)).exclude(category='PURCHASE')
    total_expenses = expenses.aggregate(t=Sum('amount'))['t'] or 0
    total_sales    = sum(r['total_sale'] for r in product_rows)
    gross_profit   = sum(r['profit']     for r in product_rows)
    net_profit     = gross_profit - float(total_expenses)
    profit_pct     = (net_profit / total_sales * 100) if total_sales else 0

    wb = openpyxl.Workbook()
    ws = wb.active
    date_label = from_date.strftime('%d %B %Y') if from_date == to_date else f"{from_date.strftime('%d %b')} - {to_date.strftime('%d %b %Y')}"
    ws.title = f"Daily Report {date_label}"[:31]

    # Title
    ws.merge_cells('A1:J1')
    title_cell = ws['A1']
    title_cell.value     = f"OCEANWAVES SEA FOODS — Report: {date_label}"
    title_cell.font      = Font(bold=True, size=14, color='1A5276', name='Calibri')
    title_cell.alignment = Alignment(horizontal='center')

    ws.merge_cells('A2:J2')
    sub_cell = ws['A2']
    sub_cell.value     = f"Store: {store.name}"
    sub_cell.font      = Font(bold=True, size=11, color='555555', name='Calibri')
    sub_cell.alignment = Alignment(horizontal='center')

    headers = ['S.No', 'Product Name', 'Opening Qty', 'Purchased Qty', 'Purchase Price',
               'Sold Qty', 'Selling Price', 'Closing Qty', 'Profit (₹)', 'Total Sale (₹)']
    _style_header(ws, 4, headers)

    alt_fill = PatternFill(start_color='EBF5FB', end_color='EBF5FB', fill_type='solid')
    for idx, row in enumerate(product_rows, 1):
        r = idx + 4
        data = [idx, row['name'], row['opening_qty'], row['purchased_qty'], row['cost_price'],
                row['sold_qty'], row['sell_price'], row['closing_qty'], row['profit'], row['total_sale']]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=r, column=col, value=val)
            cell.alignment = Alignment(horizontal='right' if col > 2 else 'left')
            if idx % 2 == 0:
                cell.fill = alt_fill

    # Summary block
    summary_row = len(product_rows) + 6
    ws.cell(row=summary_row, column=1, value='SUMMARY').font = Font(bold=True, size=12, color='1A5276', name='Calibri')
    summary_data = [
        ('Total Sales (₹)',    total_sales),
        ('Gross Profit (₹)',   gross_profit),
        ('Total Expenses (₹)', float(total_expenses)),
        ('Net Profit (₹)',     net_profit),
        ('Profit Percentage',  f"{profit_pct:.2f}%"),
    ]
    for i, (label, value) in enumerate(summary_data):
        r = summary_row + 1 + i
        lc = ws.cell(row=r, column=1, value=label)
        lc.font      = Font(bold=True, name='Calibri')
        lc.fill      = PatternFill(start_color='D6EAF8', end_color='D6EAF8', fill_type='solid')
        vc = ws.cell(row=r, column=2, value=value)
        vc.font      = Font(name='Calibri')

    # Expense details
    exp_start = summary_row + len(summary_data) + 3
    ws.cell(row=exp_start, column=1, value='EXPENSES DETAIL').font = Font(bold=True, size=12, color='1A5276')
    _style_header(ws, exp_start + 1, ['Date', 'Category', 'Type', 'Description', 'Amount (₹)'], '884EA0')
    for i, exp in enumerate(expenses):
        r = exp_start + 2 + i
        for col, val in enumerate([
            str(exp.date), exp.get_category_display(), exp.get_expense_type_display(),
            exp.description, float(exp.amount)
        ], 1):
            ws.cell(row=r, column=col, value=val)

    # Column widths
    col_widths = [6, 28, 14, 14, 14, 12, 14, 14, 14, 16]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[4].height = 30

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    file_label = from_date.isoformat() if from_date == to_date else f"{from_date.isoformat()}_to_{to_date.isoformat()}"
    response['Content-Disposition'] = f'attachment; filename="report_{file_label}.xlsx"'
    wb.save(response)
    return response


@login_required
@require_profile
def export_monthly_excel(request):
    """Export Monthly Report to Excel."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    profile = get_profile(request.user)
    store   = _get_report_store(request, profile)
    if not store or not (profile.is_superadmin or profile.is_owner or profile.is_subadmin):
        return redirect('dashboard')

    month_str = request.GET.get('month', datetime.date.today().strftime('%Y-%m'))
    try:
        year, month = map(int, month_str.split('-'))
    except ValueError:
        year, month = datetime.date.today().year, datetime.date.today().month

    m_start, m_end = _month_range(year, month)
    month_name = datetime.date(year, month, 1).strftime('%B %Y')

    sold_data = (
        SaleItem.objects.filter(sale__store=store, sale__created_at__range=(m_start, m_end))
        .values('product_id', 'product__name', 'sale__bill_type', 'product__cost_price', 'product__retail_price', 'product__stock_quantity')
        .annotate(sold_qty=Sum('quantity'), total_sale=Sum('total_amount'), profit=Sum('profit'))
    )
    purchased_data = (
        StockLog.objects.filter(store=store, movement='IN', created_at__range=(m_start, m_end))
        .values('product_id').annotate(purchased_qty=Sum('quantity'))
    )
    purchased_map = {row['product_id']: row['purchased_qty'] for row in purchased_data}
    first_snaps = {s.product_id: s for s in DailyStockSnapshot.objects.filter(store=store, date=datetime.date(year, month, 1))}
    last_snaps  = {s.product_id: s for s in DailyStockSnapshot.objects.filter(store=store, date=datetime.date(year, month, calendar.monthrange(year, month)[1]))}

    product_rows = []
    displayed_products = set()
    for row in sold_data:
        pid = row['product_id']
        bill_type = row['sale__bill_type']
        cost_price = float(row['product__cost_price'] or 0)
        sell_price = float(row['product__retail_price'] or 0)
        
        bill_type_label = "Retail" if bill_type == 'RETAIL' else "Wholesale"
        
        if pid not in displayed_products:
            opening_qty   = float(first_snaps[pid].opening_qty if pid in first_snaps else 0)
            purchased_qty = float(purchased_map.get(pid, 0))
            closing_qty   = float(last_snaps[pid].closing_qty if pid in last_snaps else float(row['product__stock_quantity'] or 0))
            displayed_products.add(pid)
        else:
            opening_qty   = 0
            purchased_qty = 0
            closing_qty   = 0
            
        product_rows.append({
            'name'         : f"{row['product__name']} ({bill_type_label})",
            'opening_qty'  : opening_qty,
            'purchased_qty': purchased_qty,
            'cost_price'   : cost_price if opening_qty > 0 or purchased_qty > 0 else 0,
            'sold_qty'     : float(row['sold_qty'] or 0),
            'sell_price'   : sell_price,
            'closing_qty'  : closing_qty,
            'profit'       : float(row['profit'] or 0),
            'total_sale'   : float(row['total_sale'] or 0),
        })

    all_exp  = Expense.objects.filter(store=store, date__year=year, date__month=month)
    total_exp = all_exp.aggregate(t=Sum('amount'))['t'] or 0
    total_sales  = sum(r['total_sale'] for r in product_rows)
    gross_profit = sum(r['profit']     for r in product_rows)
    net_profit   = gross_profit - float(total_exp)
    profit_pct   = (net_profit / total_sales * 100) if total_sales else 0

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Monthly Report {month_name}"

    ws.merge_cells('A1:J1')
    c = ws['A1']
    c.value     = f"OCEANWAVES SEA FOODS — Monthly Report: {month_name}"
    c.font      = Font(bold=True, size=14, color='1A5276', name='Calibri')
    c.alignment = Alignment(horizontal='center')
    ws.merge_cells('A2:J2')
    c2 = ws['A2']
    c2.value     = f"Store: {store.name}"
    c2.font      = Font(bold=True, size=11, color='555555', name='Calibri')
    c2.alignment = Alignment(horizontal='center')

    headers = ['S.No', 'Product Name', 'Opening Qty', 'Purchased Qty', 'Purchase Price',
               'Sold Qty', 'Selling Price', 'Closing Qty', 'Profit (₹)', 'Total Sale (₹)']
    _style_header(ws, 4, headers)
    alt_fill = PatternFill(start_color='EBF5FB', end_color='EBF5FB', fill_type='solid')
    for idx, row in enumerate(product_rows, 1):
        r = idx + 4
        data = [idx, row['name'], row['opening_qty'], row['purchased_qty'], row['cost_price'],
                row['sold_qty'], row['sell_price'], row['closing_qty'], row['profit'], row['total_sale']]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=r, column=col, value=val)
            cell.alignment = Alignment(horizontal='right' if col > 2 else 'left')
            if idx % 2 == 0:
                cell.fill = alt_fill

    summary_row = len(product_rows) + 6
    ws.cell(row=summary_row, column=1, value='MONTHLY SUMMARY').font = Font(bold=True, size=12, color='1A5276')
    for i, (label, value) in enumerate([
        ('Total Monthly Sales (₹)',    total_sales),
        ('Gross Profit (₹)',           gross_profit),
        ('Total Monthly Expenses (₹)', float(total_exp)),
        ('Net Monthly Profit (₹)',     net_profit),
        ('Profit Percentage',          f"{profit_pct:.2f}%"),
    ]):
        r = summary_row + 1 + i
        lc = ws.cell(row=r, column=1, value=label)
        lc.font = Font(bold=True)
        lc.fill = PatternFill(start_color='D6EAF8', end_color='D6EAF8', fill_type='solid')
        ws.cell(row=r, column=2, value=value)

    # All expenses
    exp_start = summary_row + 8
    ws.cell(row=exp_start, column=1, value='ALL EXPENSES').font = Font(bold=True, size=12, color='884EA0')
    _style_header(ws, exp_start + 1, ['Date', 'Category', 'Type', 'Description', 'Amount (₹)'], '884EA0')
    for i, exp in enumerate(all_exp):
        r = exp_start + 2 + i
        for col, val in enumerate([
            str(exp.date), exp.get_category_display(), exp.get_expense_type_display(),
            exp.description, float(exp.amount)
        ], 1):
            ws.cell(row=r, column=col, value=val)

    col_widths = [6, 28, 14, 14, 14, 12, 14, 14, 14, 16]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="monthly_report_{year}_{month:02d}.xlsx"'
    wb.save(response)
    return response

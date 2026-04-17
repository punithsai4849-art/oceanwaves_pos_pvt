import datetime
import calendar
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count
from django.utils import timezone
import decimal

from .models import LedgerBook, LedgerEntry, StoreAsset, Store
from .views import get_profile, require_profile


def _get_fin_store(request, profile):
    """Return the store for financial views; superadmin can pick via ?store_id="""
    if profile.is_superadmin:
        store_id = request.GET.get('store_id') or request.POST.get('store_id')
        if store_id:
            return get_object_or_404(Store, id=store_id)
        return Store.objects.filter(is_active=True).first()
    return profile.store


# ══════════════════════════════════════════════════════════════════════════════
#  LEDGER BOOKS (notebook shelf)
# ══════════════════════════════════════════════════════════════════════════════
ICON_CHOICES = [
    ('fa-book',            'Book'),
    ('fa-money-bill-wave', 'Cash'),
    ('fa-university',      'Bank'),
    ('fa-handshake',       'Supplier'),
    ('fa-shopping-cart',   'Purchase'),
    ('fa-truck',           'Transport'),
    ('fa-file-invoice',    'Invoice'),
    ('fa-piggy-bank',      'Savings'),
    ('fa-coins',           'Coins'),
    ('fa-credit-card',     'Card'),
]

COLOR_CHOICES = [
    ('#27a4d1', 'Ocean Blue'),
    ('#16a34a', 'Forest Green'),
    ('#dc2626', 'Ruby Red'),
    ('#7c3aed', 'Violet'),
    ('#d97706', 'Amber'),
    ('#0f172a', 'Midnight'),
    ('#db2777', 'Rose'),
    ('#0891b2', 'Cyan'),
]


@login_required
@require_profile
def ledger_books_list(request):
    """Notebook shelf — list of all ledger books for a store."""
    profile = get_profile(request.user)
    if not (profile.is_superadmin or profile.is_owner or profile.is_subadmin):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    store = _get_fin_store(request, profile)
    if not store:
        messages.error(request, 'No store found.')
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_book':
            name  = request.POST.get('book_name', '').strip()
            desc  = request.POST.get('book_desc', '').strip()
            color = request.POST.get('book_color', '#27a4d1')
            icon  = request.POST.get('book_icon', 'fa-book')
            if not name:
                messages.error(request, 'Book name is required.')
            else:
                LedgerBook.objects.create(
                    store=store, name=name, description=desc,
                    color=color, icon=icon, created_by=request.user
                )
                messages.success(request, f'Ledger book "{name}" created.')

        elif action == 'delete_book':
            book_id = request.POST.get('book_id')
            book = get_object_or_404(LedgerBook, id=book_id, store=store)
            name = book.name
            book.delete()
            messages.success(request, f'Ledger book "{name}" deleted along with all its entries.')

        from django.urls import reverse
        base = reverse('ledger_view')
        return redirect(f"{base}?store_id={store.id}" if profile.is_superadmin else base)

    books      = LedgerBook.objects.filter(store=store)
    all_stores = Store.objects.filter(is_active=True) if profile.is_superadmin else None

    return render(request, 'pos/ledger_books.html', {
        'profile':      profile,
        'store':        store,
        'all_stores':   all_stores,
        'books':        books,
        'icon_choices': ICON_CHOICES,
        'color_choices': COLOR_CHOICES,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  LEDGER BOOK DETAIL (entries inside one book)
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def ledger_book_detail(request, book_id):
    """Entries inside a single notebook."""
    profile = get_profile(request.user)
    if not (profile.is_superadmin or profile.is_owner or profile.is_subadmin):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    book  = get_object_or_404(LedgerBook, id=book_id)
    store = book.store

    # Non-superadmins can only view their own store's books
    if not profile.is_superadmin and profile.store != store:
        messages.error(request, 'Access denied.')
        return redirect('ledger_view')

    if request.method == 'POST':
        date_str = request.POST.get('date', timezone.now().date().isoformat())
        desc     = request.POST.get('description', '').strip()
        credit   = request.POST.get('amount_given', '').strip()
        debit    = request.POST.get('amount_spent', '').strip()

        try:
            entry_date    = datetime.date.fromisoformat(date_str)
            credit_amount = decimal.Decimal(str(credit)) if credit else decimal.Decimal('0')
            debit_amount  = decimal.Decimal(str(debit))  if debit  else decimal.Decimal('0')

            if debit_amount > 0 and not desc:
                messages.error(request, 'Explanation is mandatory for Debit entries.')
            elif credit_amount == 0 and debit_amount == 0:
                messages.error(request, 'Enter either a credit or debit amount.')
            else:
                LedgerEntry.objects.create(
                    store        = store,
                    ledger_book  = book,
                    date         = entry_date,
                    description  = desc,
                    amount_given = credit_amount,
                    amount_spent = debit_amount,
                    created_by   = request.user,
                )
                messages.success(request, 'Entry added.')
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')

        return redirect('ledger_book_detail', book_id=book.id)

    # Build entries with running balance (Newest first)
    # Using iterator() or slicing for efficiency. 
    # For a low-resource server, we should eventually paginate this.
    from django.core.paginator import Paginator
    
    entries_qs = LedgerEntry.objects.filter(ledger_book=book).order_by('date', 'created_at').only(
        'date', 'description', 'amount_given', 'amount_spent', 'created_at'
    )
    
    # Calculate global balance first (cheap via aggregate)
    current_balance = book.current_balance
    
    # Optimization: Calculate running balance only for the most recent 500 entries to save RAM
    # on this low-resource server (1GB RAM).
    total_count = entries_qs.count()
    if total_count > 500:
        entries_qs = entries_qs[total_count-500:]
        
    running = decimal.Decimal('0')
    entries = []
    
    for e in entries_qs:
        running += (e.amount_given or 0) - (e.amount_spent or 0)
        e.display_balance = running
        entries.append(e)

    entries.reverse() 
    
    # Simple pagination: 100 entries per page
    paginator = Paginator(entries, 100)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'pos/ledger_detail.html', {
        'profile':         profile,
        'store':           store,
        'book':            book,
        'entries':         page_obj,
        'current_balance': current_balance,
        'today':           datetime.date.today(),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  LEDGER ENTRY DELETE
# ══════════════════════════════════════════════════════════════════════════════
from django.views.decorators.http import require_POST as _require_POST

@_require_POST
@login_required
@require_profile
def ledger_entry_delete(request, entry_id):
    profile = get_profile(request.user)
    if not (profile.is_superadmin or profile.is_owner or profile.is_subadmin):
        messages.error(request, 'Access denied.')
        return redirect('ledger_view')

    entry = get_object_or_404(LedgerEntry, id=entry_id)

    if not profile.is_superadmin and profile.store != entry.store:
        messages.error(request, 'Access denied.')
        return redirect('ledger_view')

    book_id = entry.ledger_book_id
    entry.delete()
    messages.success(request, 'Entry deleted.')

    if book_id:
        return redirect('ledger_book_detail', book_id=book_id)
    return redirect('ledger_view')


# ══════════════════════════════════════════════════════════════════════════════
#  ASSETS
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def asset_list_view(request):
    profile = get_profile(request.user)
    if not (profile.is_superadmin or profile.is_owner):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    store = _get_fin_store(request, profile)
    if not store:
        messages.error(request, 'No store found.')
        return redirect('dashboard')

    if request.method == 'POST':
        name      = request.POST.get('name',          '').strip()
        date_str  = request.POST.get('purchase_date', timezone.now().date().isoformat())
        cost      = request.POST.get('cost',          '').strip()
        notes     = request.POST.get('notes',         '').strip()

        if not name or not cost:
            messages.error(request, 'Asset name and cost are required.')
        else:
            try:
                StoreAsset.objects.create(
                    store         = store,
                    name          = name,
                    purchase_date = datetime.date.fromisoformat(date_str),
                    cost          = decimal.Decimal(cost),
                    notes         = notes,
                    created_by    = request.user,
                )
                messages.success(request, f'Asset "{name}" added to the register.')
            except Exception as e:
                messages.error(request, f'Error: {str(e)}')

        from django.urls import reverse
        base = reverse('asset_list_view')
        redirect_url = f"{base}?store_id={store.id}" if profile.is_superadmin else base
        return redirect(redirect_url)

    assets       = StoreAsset.objects.filter(store=store).order_by('-purchase_date')
    total_assets = assets.aggregate(t=Sum('cost'))['t'] or 0
    all_stores   = Store.objects.filter(is_active=True) if profile.is_superadmin else None

    return render(request, 'pos/assets.html', {
        'profile'     : profile,
        'store'       : store,
        'all_stores'  : all_stores,
        'assets'      : assets,
        'total_assets': total_assets,
        'today'       : datetime.date.today(),
    })


@login_required
@require_profile
def asset_delete_view(request, asset_id):
    profile = get_profile(request.user)
    if not (profile.is_superadmin or profile.is_owner):
        messages.error(request, 'Access denied.')
        return redirect('asset_list_view')
    asset = get_object_or_404(StoreAsset, id=asset_id)
    if not (profile.is_superadmin or profile.store == asset.store):
        messages.error(request, 'Access denied.')
        return redirect('asset_list_view')
    asset.delete()
    messages.success(request, 'Asset removed from register.')
    return redirect('asset_list_view')

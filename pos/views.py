from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Sum, Count, Q, F
from django.views.decorators.http import require_POST
import json, decimal
from datetime import date, timedelta

from .models import Store, UserProfile, Product, Sale, SaleItem, StockLog, Expense, AreaManagerStore, WholesaleApproval, Employee, PaySlip, StockRequest, Notification
from django.core.mail import send_mail
from django.conf import settings
from django.core.cache import cache
import logging
from django.db import connection

logger = logging.getLogger(__name__)


def today_range():
    """Return (start, end) datetime range for TODAY in local timezone — fixes UTC vs IST mismatch."""
    import datetime
    from django.utils.timezone import make_aware, get_current_timezone
    tz    = get_current_timezone()
    today = date.today()
    start = make_aware(datetime.datetime.combine(today, datetime.time.min), tz)
    end   = make_aware(datetime.datetime.combine(today, datetime.time.max), tz)
    return start, end


def date_range(d):
    """Return (start, end) datetime range for a given date in local timezone."""
    import datetime
    from django.utils.timezone import make_aware, get_current_timezone
    tz    = get_current_timezone()
    start = make_aware(datetime.datetime.combine(d, datetime.time.min), tz)
    end   = make_aware(datetime.datetime.combine(d, datetime.time.max), tz)
    return start, end


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def get_profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        if user.is_superuser:
            return UserProfile.objects.create(user=user, role='SUPERADMIN')
        return None


def require_profile(view_fn):
    """Decorator: user must have a UserProfile."""
    from functools import wraps
    @wraps(view_fn)
    def inner(request, *args, **kwargs):
        p = get_profile(request.user)
        if not p:
            from django.contrib.auth import logout
            logout(request)
            messages.error(request, 'Your account has no role assigned. Contact admin.')
            return redirect('login')
        # Re-evaluate expiry on every request for temporary roles
        if p.has_expired:
            from django.contrib.auth import logout
            request.session.flush()
            logout(request)
            messages.error(request, 'Your temporary access role has expired.')
            return redirect('login')
        return view_fn(request, *args, **kwargs)
    return inner


def store_for_request(request):
    """Return the store the current user belongs to (None for superadmin)."""
    p = get_profile(request.user)
    if p and p.is_superadmin:
        return None          # superadmin has no single store
    return p.store if p else None


def assert_store_access(profile, store):
    """Return True if user can access this store."""
    if profile.is_superadmin:
        return True
    return profile.store_id == store.id


# ── Notification Helpers ──────────────────────────────────────────────────────
def create_notification(user, title, message, level='INFO', link=None):
    """Creates an in-app notification for a specific user."""
    try:
        Notification.objects.create(
            user=user, title=title, message=message, level=level, link=link
        )
    except Exception:
        pass

def send_alert_email(subject, message, recipient_list):
    """Sends an email alert to the specified recipients."""
    if not recipient_list:
        return
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list,
            fail_silently=True,
        )
    except Exception:
        pass

def notify_area_managers(store, title, message, level='INFO', link=None, include_admin=False):
    """Notifies all area managers assigned to a store, and optionally the global admin."""
    # Find all AMs for this store
    ams = UserProfile.objects.filter(
        managed_stores__store=store
    ).filter(Q(role='AREAMANAGER') | Q(role='WHOLESALE_EXEC'))
    
    recipients = []
    for am in ams:
        create_notification(am.user, title, message, level, link)
        if am.user.email:
            recipients.append(am.user.email)
    
    if include_admin and settings.ADMIN_NOTIFICATION_EMAIL:
        recipients.append(settings.ADMIN_NOTIFICATION_EMAIL)
    
    if recipients:
        send_alert_email(title, message, list(set(recipients)))


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════════════════
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        from .audit import log_event
        import time
        
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        ip       = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        lock_key = f"login_attempts:{ip}:{username}"
        attempts = cache.get(lock_key, 0)

        if attempts >= 5:
            messages.error(request, 'Too many failed attempts. Try again in 30 minutes.')
            log_event(request, 'LOGIN_LOCKED', f'username={username}', level='WARNING')
            return render(request, 'pos/login.html')

        user = authenticate(request, username=username, password=password)
        if user:
            profile_obj = get_profile(user)
            if profile_obj and not profile_obj.has_expired:
                cache.delete(lock_key)
                login(request, user)
                log_event(request, 'LOGIN_SUCCESS', f'username={user.username}')

            # ── Record Attendance ──────────────────────────────────────────
            import datetime as dt
            from .models import LoginAttendance
            profile_obj = get_profile(user)
            now_local   = timezone.localtime(timezone.now())

            try:
                LoginAttendance.objects.create(
                    store      = profile_obj.store,
                    user       = user,
                    login_date = now_local.date(),
                    login_time = now_local.time(),
                    ip_address = ip[:50],
                )
            except Exception:
                pass
            # ──────────────────────────────────────────────────────────────

            return redirect('dashboard')

        else:
            cache.set(lock_key, attempts + 1, timeout=1800)  # 30-min window
            time.sleep(0.3) # Throttle to slow down brute force
            log_event(request, 'LOGIN_FAILED', f'username={username}', level='WARNING')
            messages.error(request, 'Invalid username or password.')
            
    return render(request, 'pos/login.html')


def logout_view(request):
    from .audit import log_event
    if request.user.is_authenticated:
        log_event(request, 'LOGOUT', f'username={request.user.username}')
    request.session.flush()
    logout(request)
    return redirect('login')


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def dashboard(request):
    from django.utils import timezone
    from datetime import timedelta, date
    from django.db.models import Sum, Count, Q
    
    today = date.today()
    tr_start, tr_end = today_range()
    profile = get_profile(request.user)
    if not profile:
        return redirect('login')
        
    if profile.is_superadmin or profile.is_area_manager or profile.is_subadmin:
        template_name = 'pos/dashboard_admin.html'
    else:
        template_name = 'pos/dashboard_store.html'

    try:
        from .models import Store, Sale, SaleItem, CreditRecord, Product
        context = {}
        
        # Helper maps for today's stats
        bill_counts = Sale.objects.filter(created_at__gte=tr_start, created_at__lte=tr_end).values('store_id').annotate(c=Count('id'))
        bill_map = {item['store_id']: item['c'] for item in bill_counts}

        sales_agg = SaleItem.objects.filter(sale__created_at__gte=tr_start, sale__created_at__lte=tr_end).values('sale__store_id').annotate(t_sales=Sum('total_amount'), t_profit=Sum('profit'))
        sales_map = {item['sale__store_id']: {'s': item['t_sales'], 'p': item['t_profit']} for item in sales_agg}

        # Determine stores to show
        if profile.is_superadmin or profile.is_subadmin:
            stores = Store.objects.filter(is_active=True)
            my_store_ids = list(stores.values_list('id', flat=True))
        elif profile.is_area_manager:
            from .models import AreaManagerStore
            my_store_ids = list(AreaManagerStore.objects.filter(manager=profile).values_list('store_id', flat=True))
            stores = Store.objects.filter(id__in=my_store_ids, is_active=True)
        else:
            stores = Store.objects.filter(id=profile.store.id)
            my_store_ids = [profile.store.id]

        # Aggregates for display
        total_b = total_s = total_p = 0
        store_data = []
        for s in stores:
            b_c = bill_map.get(s.id, 0)
            s_s = sales_map.get(s.id, {}).get('s', 0) or 0
            s_p = sales_map.get(s.id, {}).get('p', 0) or 0
            total_b += b_c
            total_s += s_s
            total_p += s_p
            store_data.append({
                'store': s,
                'bill_count': b_c,
                'total_sales': s_s,
                'total_profit': s_p,
                'out_stock': Product.objects.filter(store=s, is_active=True, stock_quantity__lte=0).count(),
                'low_stock': Product.objects.filter(store=s, is_active=True, stock_quantity__gt=0, stock_quantity__lte=F('low_stock_alert')).count(),
            })

        context.update({
            'total_stores': len(stores),
            'total_bills': total_b,
            'global_sales': total_s,
            'global_profit': total_p,
            'store_data': store_data,
        })

        if not (profile.is_superadmin or profile.is_area_manager or profile.is_subadmin):
            # Store-Specific Stats
            store = profile.store
            context.update({
                'today_bills': total_b,
                'today_sales': total_s,
                'today_profit': total_p,
                'today_cost': total_s - total_p,
                'store': store,
            })
            
            # Stock alerts for store
            low_p = Product.objects.filter(store=store, is_active=True).filter(Q(stock_quantity__lte=0) | Q(stock_quantity__lte=F('low_stock_alert'))).order_by('stock_quantity')
            context['low_products'] = low_p[:5]
            context['out_count'] = Product.objects.filter(store=store, is_active=True, stock_quantity__lte=0).count()
            context['low_count'] = Product.objects.filter(store=store, is_active=True, stock_quantity__gt=0, stock_quantity__lte=F('low_stock_alert')).count()

            # Week Summary
            week_ago = timezone.now() - timedelta(days=7)
            week_agg = SaleItem.objects.filter(sale__store=store, sale__created_at__gte=week_ago).aggregate(s=Sum('total_amount'), p=Sum('profit'))
            context['week_sales'] = week_agg['s'] or 0
            context['week_profit'] = week_agg['p'] or 0

        # Recent Bills (Common)
        recent_q = Sale.objects.filter(created_at__gte=tr_start, created_at__lte=tr_end).order_by('-created_at')
        if not profile.is_superadmin:
            recent_q = recent_q.filter(store_id__in=my_store_ids)
        context['recent_sales'] = recent_q[:10]

        # Urgent vs Pending Credits
        due_threshold = today + timedelta(days=2) # 48 hours
        credits_q = CreditRecord.objects.select_related('customer', 'sale__store').filter(is_paid=False).order_by('due_date')
        if not profile.is_superadmin:
            credits_q = credits_q.filter(sale__store_id__in=my_store_ids)
        
        context['urgent_credits'] = credits_q.filter(due_date__lte=due_threshold)[:10]
        context['pending_credits'] = credits_q.filter(due_date__gt=due_threshold)[:10]

        return render(request, template_name, context)

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Dashboard error: {e}")
        return render(request, template_name, {"error": str(e)})

    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return render(request, template_name, {})



# ══════════════════════════════════════════════════════════════════════════════
#  STORE MANAGEMENT  (superadmin only)
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def store_list(request):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    cache_key = 'pos_admin_stores_list'
    cached_data = None
    try:
        cached_data = cache.get(cache_key)
    except Exception:
        pass

    if not cached_data:
        stores_qs = Store.objects.filter(is_active=True).order_by('name')
        # Annotate separately to avoid JOIN multiplication
        stores_with_staff    = {s.id: s.staff_count    for s in stores_qs.annotate(staff_count=Count('staff',    filter=Q(staff__is_active=True),    distinct=True))}
        stores_with_products = {s.id: s.product_count  for s in stores_qs.annotate(product_count=Count('products', filter=Q(products__is_active=True), distinct=True))}
        stores = list(stores_qs)
        for s in stores:
            s.staff_count   = stores_with_staff.get(s.id, 0)
            s.product_count = stores_with_products.get(s.id, 0)
        
        cached_data = {'stores': stores}
        try:
            cache.set(cache_key, cached_data, timeout=60)
        except Exception:
            pass

    return render(request, 'pos/stores.html', {'stores': cached_data['stores'], 'profile': profile})


@login_required
@require_profile
def store_create(request):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return redirect('dashboard')
    if request.method == 'POST':
        Store.objects.create(
            name=request.POST['name'].strip(),
            address=request.POST.get('address', '').strip(),
            phone=request.POST.get('phone', '').strip(),
            whatsapp_number=request.POST.get('whatsapp_number', '').strip(),
            email=request.POST.get('email', '').strip(),
            gstin=request.POST.get('gstin', '').strip(),
            upi_id=request.POST.get('upi_id', '').strip(),
        )
        messages.success(request, f'Store "{request.POST["name"]}" created!')
    return redirect('store_list')


@login_required
@require_profile
def store_edit(request, store_id):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return redirect('dashboard')
    store = get_object_or_404(Store, id=store_id)
    if request.method == 'POST':
        store.name            = request.POST.get('name', store.name).strip()
        store.code            = request.POST.get('code', store.code).strip().upper() if request.POST.get('code') else store.code
        store.address         = request.POST.get('address', store.address).strip()
        store.phone           = request.POST.get('phone', store.phone).strip()
        store.whatsapp_number = request.POST.get('whatsapp_number', store.whatsapp_number).strip()
        store.email           = request.POST.get('email', store.email).strip()
        store.gstin           = request.POST.get('gstin', store.gstin).strip()
        store.upi_id          = request.POST.get('upi_id', store.upi_id).strip()
        store.is_active       = request.POST.get('is_active') == 'on'
        store.save()
        messages.success(request, 'Store updated.')
    return redirect('store_list')


@login_required
@require_profile
def store_detail(request, store_id):
    """Admin view into a specific store."""
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return redirect('dashboard')
    store   = get_object_or_404(Store, id=store_id)
    today   = date.today()
    report_date_str = request.GET.get('date', '')
    try:
        report_date = date.fromisoformat(report_date_str) if report_date_str else today
    except ValueError:
        report_date = today

    r_start, r_end = date_range(report_date)

    items_qs = SaleItem.objects.filter(
        sale__store=store, sale__created_at__range=(r_start, r_end))
    agg = items_qs.aggregate(
        sales=Sum('total_amount'), cost=Sum('total_cost'), profit=Sum('profit'))

    # Month range
    import datetime
    from django.utils.timezone import make_aware, get_current_timezone
    tz = get_current_timezone()
    month_start = make_aware(datetime.datetime(report_date.year, report_date.month, 1, 0, 0, 0), tz)
    import calendar
    last_day = calendar.monthrange(report_date.year, report_date.month)[1]
    month_end = make_aware(datetime.datetime(report_date.year, report_date.month, last_day, 23, 59, 59), tz)
    month_agg = SaleItem.objects.filter(
        sale__store=store, sale__created_at__range=(month_start, month_end)
    ).aggregate(sales=Sum('total_amount'), profit=Sum('profit'))

    ctx = {
        'profile':       profile,
        'store':         store,
        'report_date':   report_date,
        'sales':         Sale.objects.filter(store=store, created_at__range=(r_start, r_end)),
        'sale_items':    items_qs,
        'total_sales':   agg['sales']         or 0,
        'total_cost':    agg['cost']          or 0,
        'total_profit':  agg['profit']        or 0,
        'month_sales':   month_agg['sales']   or 0,
        'month_profit':  month_agg['profit']  or 0,
        'products':      Product.objects.filter(store=store, is_active=True),
        'staff':         UserProfile.objects.filter(store=store, is_active=True).select_related('user'),
        'expenses':      Expense.objects.filter(store=store, date=report_date),
    }
    return render(request, 'pos/store_detail.html', ctx)


# ══════════════════════════════════════════════════════════════════════════════
#  USER MANAGEMENT  (superadmin only)
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def user_management(request):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    users  = UserProfile.objects.select_related('user', 'store').order_by('store__name', 'role')
    
    store_filter = request.GET.get('store_filter')
    if store_filter:
        if store_filter.isdigit():
            users = users.filter(store_id=store_filter)
        elif store_filter == 'none':
            users = users.filter(store__isnull=True)
            
    stores = Store.objects.filter(is_active=True)
    cache_key = f'pos_admin_users_{store_filter or "all"}'
    cached_data = None
    try:
        cached_data = cache.get(cache_key)
    except Exception:
        pass

    if not cached_data:
        users_list = list(users[:200]) # Evaluate and cap
        stores_list = list(stores)     # Evaluate
        
        cached_data = {
            'users': users_list,
            'stores': stores_list,
        }
        try:
            cache.set(cache_key, cached_data, timeout=60)
        except Exception:
            pass

    return render(request, 'pos/users.html', {
        'users':        cached_data['users'], 
        'stores':       cached_data['stores'], 
        'profile':      profile,
        'store_filter': store_filter
    })


@login_required
@require_profile
def user_create(request):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return redirect('dashboard')
    if request.method == 'POST':
        username   = request.POST.get('username', '').strip()
        password   = request.POST.get('password', '')
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        email      = request.POST.get('email', '').strip()
        role       = request.POST.get('role', 'STAFF')
        store_id   = request.POST.get('store_id')
        
        # Upper management don't belong to a specific store
        if role in ('AREAMANAGER', 'WHOLESALE_EXEC', 'SUBADMIN', 'SUPERADMIN'):
            store_id = None

        if not username:
            messages.error(request, 'Username is required.')
        elif not email:
            messages.error(request, 'Email address is required.')
        elif User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" is already taken.')
        elif User.objects.filter(email=email).exists():
            messages.error(request, f'Email "{email}" is already taken by another account.')
        elif role in ('STAFF', 'OWNER') and (not store_id or store_id == 'None'):
            messages.error(request, 'Primary Store is required for this role to create their Employee record.')
            return redirect('user_management')
        else:
            ALLOWED_ROLES = ['SUBADMIN', 'OWNER', 'STAFF', 'AREAMANAGER', 'WHOLESALE_EXEC', 'SUPERADMIN']
            if role not in ALLOWED_ROLES:
                messages.error(request, 'Invalid role assignment.')
                return redirect('user_management')
                
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError
            from .audit import log_event
            
            try:
                validate_password(password)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
                return redirect('user_management')

            u = User.objects.create_user(username=username, password=password, email=email,
                                         first_name=first_name, last_name=last_name)
            if role == 'SUPERADMIN':
                u.is_superuser = True
                u.is_staff = True
                u.save()
            
            log_event(request, 'USER_CREATED', f'created_user={username} role={role}')
            
            store = None
            if store_id and store_id != 'None':
                store = Store.objects.filter(id=store_id).first()
            phone = request.POST.get('phone', '').strip()

            expires_at_str = request.POST.get('expires_at', '').strip()
            expires_at = None
            if expires_at_str:
                import datetime
                from django.utils import timezone
                try:
                    expires_at = timezone.make_aware(datetime.datetime.strptime(expires_at_str, '%Y-%m-%dT%H:%M'))
                except Exception:
                    pass

            permissions = {}
            if role == 'SUBADMIN':
                permissions = {
                    'manage_users': request.POST.get('perm_manage_users') == 'on',
                    'view_reports': request.POST.get('perm_view_reports') == 'on',
                    'manage_inventory': request.POST.get('perm_manage_inventory') == 'on',
                }

            UserProfile.objects.create(
                user=u, role=role, store=store, phone=phone,
                expires_at=expires_at, permissions=permissions
            )
            role_label = dict(UserProfile.ROLE_CHOICES).get(role, role)
            messages.success(request, f'User "{username}" created as {role_label}.')
            if role in ('AREAMANAGER', 'WHOLESALE_EXEC'):
                messages.info(request, f'Go to Area Managers page to assign stores and set a PIN for {username}.')

            # ── Auto-create Employee record for store-based roles ──
            if role != 'SUPERADMIN':
                import datetime as _dt
                full_name     = f"{first_name} {last_name}".strip() or username
                designation   = request.POST.get('designation', '').strip()
                emp_type      = request.POST.get('employment_type', 'FULLTIME')
                basic_salary  = request.POST.get('basic_salary', '0').strip() or '0'
                allowances    = request.POST.get('allowances', '0').strip() or '0'
                deductions    = request.POST.get('deductions', '0').strip() or '0'
                date_str      = request.POST.get('date_joined', '').strip()
                try:
                    date_joined = _dt.date.fromisoformat(date_str) if date_str else _dt.date.today()
                except ValueError:
                    date_joined = _dt.date.today()
                from .models import Employee
                
                if store:
                    last_emp = Employee.objects.filter(store=store).order_by('-id').first()
                    num      = (last_emp.id + 1) if last_emp else 1
                    emp_id   = f"EMP{store.id}{str(num).zfill(4)}"
                else:
                    last_emp = Employee.objects.filter(store__isnull=True).order_by('-id').first()
                    num      = (last_emp.id + 1) if last_emp else 1
                    emp_id   = f"HO{str(num).zfill(4)}"
                    
                up_obj   = UserProfile.objects.get(user=u)
                Employee.objects.create(
                    store           = store,
                    user_profile    = up_obj,
                    employee_id     = emp_id,
                    full_name       = full_name,
                    phone           = phone,
                    email           = email,
                    designation     = designation,
                    employment_type = emp_type,
                    basic_salary    = basic_salary,
                    allowances      = allowances,
                    deductions      = deductions,
                    date_joined     = date_joined,
                    created_by      = request.user,
                )
                messages.info(request, f'Employee record created automatically for "{full_name}".')

    return redirect('user_management')


@login_required
@require_profile
def user_edit(request, user_id):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return redirect('dashboard')
    up = get_object_or_404(UserProfile, id=user_id)
    if request.method == 'POST':
        up.role      = request.POST.get('role', up.role)
        store_id     = request.POST.get('store_id', '').strip()
        up.store     = Store.objects.filter(id=store_id).first() if (store_id and store_id != 'None') else None
        up.is_active = request.POST.get('is_active') == 'on'
        up.phone     = request.POST.get('phone', '').strip()

        expires_at_str = request.POST.get('expires_at', '').strip()
        if expires_at_str:
            import datetime
            from django.utils import timezone
            try:
                up.expires_at = timezone.make_aware(datetime.datetime.strptime(expires_at_str, '%Y-%m-%dT%H:%M'))
            except Exception:
                pass
        else:
            up.expires_at = None

        if up.role == 'SUBADMIN':
            up.permissions = {
                'manage_users': request.POST.get('perm_manage_users') == 'on',
                'view_reports': request.POST.get('perm_view_reports') == 'on',
                'manage_inventory': request.POST.get('perm_manage_inventory') == 'on',
            }

        new_email = request.POST.get('email', '').strip()
        if new_email and new_email != up.user.email:
            if User.objects.filter(email=new_email).exclude(id=up.user.id).exists():
                messages.error(request, f'Email "{new_email}" is already used by another account.')
                return redirect('user_management')
            up.user.email = new_email

        up.save()
        up.user.first_name = request.POST.get('first_name', up.user.first_name).strip()
        up.user.last_name  = request.POST.get('last_name',  up.user.last_name).strip()
        up.user.is_active  = up.is_active
        new_password = request.POST.get('password', '').strip()
        if new_password:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError
            try:
                validate_password(new_password)
                up.user.set_password(new_password)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
                return redirect('user_management')

        up.user.save()
        messages.success(request, f'User "{up.user.username}" updated successfully.')

    return redirect('user_management')


@require_POST
@login_required
@require_profile
def user_delete(request, user_id):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return redirect('dashboard')
    up = get_object_or_404(UserProfile, id=user_id)
    if up.user == request.user:
        messages.error(request, "Can't delete your own account.")
    else:
        from .audit import log_event
        del_user = up.user.username
        up.user.delete()
        log_event(request, 'USER_DELETED', f'deleted_user={del_user}')
        messages.success(request, 'User deleted.')
    return redirect('user_management')


# ══════════════════════════════════════════════════════════════════════════════
#  BILLING
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def billing(request):
    profile = get_profile(request.user)
    store   = profile.store
    if not store:
        messages.error(request, 'Not assigned to a store.')
        return redirect('dashboard')
    products = Product.objects.filter(store=store, is_active=True).order_by('category', 'name')
    from .models import WholesaleCustomer
    w_customers = WholesaleCustomer.objects.all()
    return render(request, 'pos/billing.html', {
        'products': products, 
        'store': store, 
        'profile': profile,
        'wholesale_customers': w_customers
    })


from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
import uuid
from .phonepe_integration import PhonePeGateway

@require_POST
@login_required
@require_profile
def phonepe_initiate(request):
    """
    Creates a transaction on PhonePe and returns the payment data.
    """
    profile = get_profile(request.user)
    try:
        data = json.loads(request.body)
        amount = data.get('amount')
        if not amount or float(amount) <= 0:
            return JsonResponse({'success': False, 'error': 'Invalid amount.'})
        
        # Unique transaction ID: S{store_id}_{uuid}
        store_id = profile.store.id if profile.store else 0
        tx_id = f"S{store_id}_{uuid.uuid4().hex[:12].upper()}"
        
        pg = PhonePeGateway()
        response = pg.initiate_payment(
            transaction_id=tx_id,
            user_id=request.user.id,
            amount_in_rupees=amount
        )
        
        if response.get('success'):
            # Return transaction id and the data for QR (either direct intent or URL)
            return JsonResponse({
                'success': True,
                'transaction_id': tx_id,
                'data': response['data']
            })
        else:
            return JsonResponse({
                'success': False,
                'error': response.get('message', 'Failed to initiate payment')
            })
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@require_profile
def phonepe_status(request):
    """
    Checks the status of a specific PhonePe transaction.
    """
    tx_id = request.GET.get('transaction_id')
    if not tx_id:
        return JsonResponse({'success': False, 'error': 'Transaction ID missing.'})
    
    try:
        pg = PhonePeGateway()
        response = pg.check_status(tx_id)
        
        # PhonePe returns SUCCESS, FAILURE, PENDING
        if response.get('success') and response.get('code') == 'PAYMENT_SUCCESS':
            return JsonResponse({'success': True, 'status': 'SUCCESS'})
        elif response.get('code') == 'PAYMENT_PENDING':
            return JsonResponse({'success': True, 'status': 'PENDING'})
        else:
            return JsonResponse({'success': True, 'status': 'FAILED', 'message': response.get('message')})
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_POST
@login_required
@require_profile
@ratelimit(key='ip', rate='10/m', block=True)
def save_bill(request):
    profile = get_profile(request.user)
    store   = profile.store
    if not store:
        return JsonResponse({'success': False, 'error': 'Not assigned to a store.'})

    # JSON Bombing Prevention (1MB max body)
    if len(request.body) > 1024 * 1024:
        return JsonResponse({'success': False, 'error': 'Payload too large.'}, status=400)

    try:
        data       = json.loads(request.body)
        items_data = data.get('items', [])
        bill_type  = data.get('bill_type', 'RETAIL')
        payment    = data.get('payment_mode', 'CASH')
        
        # Helper for strictly validating numeric bounds
        def validated_decimal(val, min_val=0, max_val=9999999):
            try:
                d = decimal.Decimal(str(val))
                if d < min_val or d > max_val:
                    raise ValueError
                return d
            except:
                raise ValueError("Invalid numeric value provided.")
                
        gst_rate = validated_decimal(data.get('gst_rate', 0), 0, 100)
        discount = validated_decimal(data.get('discount', 0), 0, 9999999)

        if not items_data:
            return JsonResponse({'success': False, 'error': 'No items in bill.'})

        # Validate products belong to this store & check stock
        validated = []
        for it in items_data:
            product = get_object_or_404(Product, id=it['product_id'], store=store)
            qty = validated_decimal(it['quantity'], 0.001, 99999)

            # Anyone is allowed to change prices now
            can_manage_prices = True
            
            # Use the price sent from the billing UI only if authorized
            client_price = it.get('selling_price')
            if client_price is not None and can_manage_prices:
                sp = validated_decimal(client_price, 0, 9999999)
            elif bill_type == 'WHOLESALE':
                sp = product.wholesale_price
            else:
                sp = product.retail_price

            # Temporarily disabled strict stock check for manual backdating of sales data
            # if product.stock_quantity < qty:
            #     return JsonResponse({'success': False,
            #         'error': f'Insufficient stock for {product.name}. Available: {product.stock_quantity} kg'})
            validated.append((product, qty, sp))

        # Build Sale
        sale = Sale(store=store, bill_type=bill_type, payment_mode=payment,
                    gst_rate=gst_rate, discount=discount, created_by=request.user)
        
        bill_date_str = data.get('bill_date')
        if bill_date_str:
            try:
                from django.utils.dateparse import parse_date
                import datetime
                parsed_date = parse_date(bill_date_str)
                if parsed_date:
                    current_time = timezone.now().time()
                    naive_datetime = datetime.datetime.combine(parsed_date, current_time)
                    sale.created_at = timezone.make_aware(naive_datetime, timezone.get_current_timezone())
            except Exception as e:
                pass
        
        cname = data.get('customer_name', '').strip()
        cphone = data.get('customer_phone', '').strip()
        
        wc = None
        if bill_type == 'WHOLESALE':
            sale.customer_name    = cname
            sale.customer_phone   = cphone
            sale.customer_gst     = data.get('customer_gst', '').strip()
            sale.customer_address = data.get('customer_address', '').strip()

            from .models import WholesaleCustomer, CreditRecord
            if cname:
                # Lookup by Name OR Customer Code
                wc = WholesaleCustomer.objects.filter(
                    Q(name__iexact=cname) | Q(customer_code__iexact=cname)
                ).first()
                if payment == 'CREDIT':
                    if not wc:
                        wc = WholesaleCustomer.objects.create(
                            name=cname, phone=sale.customer_phone, gst=sale.customer_gst,
                            address=sale.customer_address,
                            is_credit_enabled=True, credit_duration_days=7, created_by=request.user
                        )
                    else:
                        if not wc.is_credit_enabled:
                            return JsonResponse({'success': False, 'error': f'Credit is disabled for {cname}.'})
                        # if wc.has_unpaid_credit:
                        #     return JsonResponse({'success': False, 'error': f'{cname} has an outstanding credit balance of ₹{wc.balance}. Please settle it before making new credit sales.'})
                
                sale.wholesale_customer = wc
        else:
            # Capture retail customer info too (for WhatsApp billing)
            sale.customer_name  = cname
            sale.customer_phone = cphone


        subtotal = sum(q * sp for _, q, sp in validated)
        sale.subtotal = subtotal - discount

        if bill_type == 'WHOLESALE' and gst_rate > 0:
            half             = gst_rate / decimal.Decimal('2')
            sale.cgst_amount = (sale.subtotal * half / 100).quantize(decimal.Decimal('0.01'))
            sale.sgst_amount = sale.cgst_amount
            sale.total_gst   = sale.cgst_amount + sale.sgst_amount
            sale.grand_total = sale.subtotal + sale.total_gst
        else:
            sale.grand_total = sale.subtotal

        sale.save()
        
        # Audit Logging
        from .audit import log_event
        log_event(request, 'BILL_CREATED', f'bill={sale.bill_number} amount={sale.grand_total} mode={payment}')

        if payment == 'CREDIT' and wc:
            from datetime import timedelta
            cr = CreditRecord.objects.create(
                customer=wc, sale=sale,
                due_date=sale.created_at.date() + timedelta(days=wc.credit_duration_days)
            )
            # Force created_at to match the backdated sale timestamp
            CreditRecord.objects.filter(id=cr.id).update(created_at=sale.created_at)

        # Create SaleItems + deduct stock
        for product, qty, sp in validated:
            cp = product.cost_price
            SaleItem.objects.create(
                sale=sale, product=product, product_name=product.name,
                quantity=qty, cost_price=cp, selling_price=sp,
                total_amount=qty*sp, total_cost=qty*cp, profit=qty*(sp-cp)
            )
            
            old_qty = product.stock_quantity
            product.stock_quantity -= qty
            product.save(update_fields=['stock_quantity'])
            
            # ── Low Stock Trigger ──
            if old_qty > product.low_stock_alert and product.stock_quantity <= product.low_stock_alert:
                notify_area_managers(
                    store=store,
                    title="⚠️ Low Stock Alert",
                    message=f"Product '{product.name}' has reached low stock ({product.stock_quantity} kg remaining) at {store.name}.",
                    level='WARNING',
                    link='/inventory/'
                )

            StockLog.objects.create(
                store=store, product=product, movement='OUT',
                quantity=qty, balance=product.stock_quantity,
                reference=sale.bill_number, created_by=request.user,
                created_at=sale.created_at
            )

        # Build WhatsApp message & URL
        import urllib.parse
        items_summary = "\n".join(
            f"  • {p.name}: {q}kg × ₹{sp} = ₹{q*sp:.2f}"
            for p, q, sp in validated
        )
        wa_msg = (
            f"🌊 *OCEANWAVES SEA FOODS*\n"
            f"_{store.name} — Retail Bill_\n\n"
            f"Bill No: *{sale.bill_number}*\n"
            f"Total: *Rs.{sale.grand_total}*\n"
            f"Mode: {sale.get_payment_mode_display()}\n\n"
            f"OCEANWAVES SEA FOODS is part of OCEANWAVES VICTUALS PRIVATE LIMITED.\n"
            f"Thank you!"
        )
        
        # Build whatsapp URL — send to customer if they gave their number,
        # otherwise fall back to the store's own WhatsApp number
        customer_phone = sale.customer_phone.strip().lstrip('+').replace(' ', '').replace('-', '')
        store_wa = (store.whatsapp_number or '').strip().lstrip('+').replace(' ', '').replace('-', '')
        wa_phone = customer_phone if customer_phone else store_wa
        encoded_msg = urllib.parse.quote(wa_msg)
        whatsapp_url = f"https://wa.me/{wa_phone}?text={encoded_msg}" if wa_phone else None
        
        return JsonResponse({
            'success': True,
            'bill_id': sale.id,
            'bill_number': sale.bill_number,
            'whatsapp_url': whatsapp_url,
        })

    except Exception as e:
        from .audit import log_event
        log_event(request, 'BILL_API_ERROR', str(e), level='ERROR')
        # Mask exact internal errors
        return JsonResponse({'success': False, 'error': 'An internal error occurred while saving the bill.'})


@login_required
@require_profile
def bill_print(request, bill_id):
    profile = get_profile(request.user)
    sale    = get_object_or_404(Sale, id=bill_id)
    if not assert_store_access(profile, sale.store):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    return render(request, 'pos/bill_print.html', {'sale': sale})


# ══════════════════════════════════════════════════════════════════════════════
#  INVENTORY
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@login_required
@require_profile
def inventory(request):
    profile  = get_profile(request.user)
    store    = profile.store
    managed_stores = []

    # Area Managers manage stores via AreaManagerStore
    if profile.is_area_manager and not store:
        managed_stores_qs = AreaManagerStore.objects.filter(
            manager=profile
        ).select_related('store').only('store__id', 'store__name').order_by('store__name')
        
        managed_stores = list(managed_stores_qs)

        store_id = request.GET.get('store_id')
        if store_id:
            ams_entry = next((ams for ams in managed_stores if str(ams.store_id) == str(store_id)), None)
            if ams_entry:
                store = ams_entry.store
        if not store and managed_stores:
            store = managed_stores[0].store

    if not store:
        messages.error(request, 'Not assigned to any store.')
        return redirect('dashboard')

    # We evaluate QuerySets with field limiting for performance, but we do not cache
    # to ensure newly added products appear immediately.
    products = list(Product.objects.filter(store=store, is_active=True).select_related('store').only(
        'store_id', 'name', 'retail_price', 'wholesale_price', 'cost_price', 
        'stock_quantity', 'low_stock_alert', 'is_active'
    ))

    return render(request, 'pos/inventory.html', {
        'profile': profile,
        'store': store,
        'products': products,
        'managed_stores': managed_stores,
    })


@login_required
@require_profile
def product_add(request):
    profile = get_profile(request.user)
    store   = profile.store

    # Area Managers: resolve store from POST data or AreaManagerStore
    if profile.is_area_manager and not store:
        store_id = request.POST.get('store_id') or request.GET.get('store_id')
        if store_id:
            ams = AreaManagerStore.objects.filter(manager=profile, store_id=store_id).first()
            if ams:
                store = ams.store
        if not store:
            first = AreaManagerStore.objects.filter(manager=profile).select_related('store').first()
            if first:
                store = first.store

    if not store:
        messages.error(request, 'Not assigned to a store.')
        return redirect('employee_list')

    # Anyone who can add a product can set prices
    can_manage_prices = True

    if request.method == 'POST':
        p = Product(
            store=store,
            name=request.POST['name'].strip(),
            category=request.POST.get('category', 'FISH'),
            barcode=request.POST.get('barcode', '').strip(),
            cost_price=request.POST.get('cost_price', 0) if can_manage_prices else 0,
            retail_price=request.POST.get('retail_price', 0) if can_manage_prices else 0,
            wholesale_price=request.POST.get('wholesale_price', 0) if can_manage_prices else 0,
            stock_quantity=request.POST.get('stock_quantity', 0),
            low_stock_alert=request.POST.get('low_stock_alert', 5),
        )
        p.save()

        if not can_manage_prices:
            messages.warning(request, "Product added, but prices must be set by an Area Manager or Admin.")

        if float(p.stock_quantity) > 0:
            StockLog.objects.create(store=store, product=p, movement='IN',
                quantity=p.stock_quantity, balance=p.stock_quantity,
                reference='Initial stock', created_by=request.user)
        messages.success(request, f'Product "{p.name}" added successfully.')
    return redirect(f'/inventory/?store_id={store.id}' if profile.is_area_manager else 'inventory')


@login_required
@require_profile
def product_edit(request, pid):
    profile = get_profile(request.user)

    # Area Managers may not have profile.store — verify via AreaManagerStore
    if profile.is_superadmin:
        p = get_object_or_404(Product, id=pid)
    elif profile.is_area_manager and not profile.store:
        p = get_object_or_404(Product, id=pid)
        has_access = AreaManagerStore.objects.filter(
            manager=profile, store=p.store
        ).exists()
        if not has_access:
            messages.error(request, 'Access denied: you do not manage this store.')
            return redirect('inventory')
    else:
        p = get_object_or_404(Product, id=pid, store=profile.store)

    # Anyone who can edit a product can set prices
    can_manage_prices = True

    if request.method == 'POST':
        p.name     = request.POST.get('name', p.name).strip()
        p.category = request.POST.get('category', p.category)

        if can_manage_prices:
            p.cost_price      = request.POST.get('cost_price', p.cost_price)
            p.retail_price    = request.POST.get('retail_price', p.retail_price)
            p.wholesale_price = request.POST.get('wholesale_price', p.wholesale_price)

        p.low_stock_alert = request.POST.get('low_stock_alert', p.low_stock_alert)
        
        new_stock = request.POST.get('stock_quantity')
        if new_stock is not None and str(new_stock).strip() != '':
            new_stock = decimal.Decimal(new_stock)
            if new_stock != p.stock_quantity:
                diff = new_stock - p.stock_quantity
                p.stock_quantity = new_stock
                StockLog.objects.create(
                    store=p.store,
                    product=p,
                    movement='IN' if diff > 0 else 'OUT',
                    quantity=abs(diff),
                    balance=p.stock_quantity,
                    reference='Manual Adjustment via Edit',
                    created_by=request.user
                )

        p.save()
        messages.success(request, f'"{p.name}" updated.')
    return redirect(f'/inventory/?store_id={p.store_id}' if profile.is_area_manager else 'inventory')


@login_required
@require_profile
def product_restock(request, pid):
    profile = get_profile(request.user)

    if profile.is_superadmin:
        p = get_object_or_404(Product, id=pid)
    elif profile.is_area_manager and not profile.store:
        p = get_object_or_404(Product, id=pid)
        has_access = AreaManagerStore.objects.filter(
            manager=profile, store=p.store
        ).exists()
        if not has_access:
            messages.error(request, 'Access denied.')
            return redirect('inventory')
    else:
        p = get_object_or_404(Product, id=pid, store=profile.store)

    store = p.store
    if request.method == 'POST':
        qty = decimal.Decimal(request.POST.get('add_quantity', 0))
        restock_date_str = request.POST.get('restock_date')
        log_expense = request.POST.get('log_expense') == 'on'
        note = request.POST.get('note', '').strip()
        
        import datetime
        from django.utils import timezone
        
        target_date = timezone.now().date()
        target_dt = timezone.now()
        
        if restock_date_str:
            try:
                parsed_date = datetime.date.fromisoformat(restock_date_str)
                target_date = parsed_date
                current_time = timezone.now().time()
                naive_dt = datetime.datetime.combine(parsed_date, current_time)
                target_dt = timezone.make_aware(naive_dt, timezone.get_current_timezone())
            except ValueError:
                pass

        if qty != 0:
            p.stock_quantity += qty
            p.save(update_fields=['stock_quantity'])
            
            log = StockLog.objects.create(
                store=store, product=p, 
                movement='IN' if qty > 0 else 'OUT',
                quantity=abs(qty), balance=p.stock_quantity,
                reference=note or ('Stock In' if qty > 0 else 'Stock Out'),
                created_by=request.user
            )
            # Force created_at timestamp
            StockLog.objects.filter(id=log.id).update(created_at=target_dt)
            
            if log_expense and qty > 0:
                cost_amount = qty * p.cost_price
                if cost_amount > 0:
                    exp_desc = f"Stock Purchase: {qty} kg of {p.name}"
                    if note:
                        exp_desc += f" ({note})"
                    
                    Expense.objects.create(
                        store=store,
                        category='PURCHASE',
                        description=exp_desc,
                        amount=cost_amount,
                        date=target_date,
                        created_by=request.user
                    )
                    
        messages.success(request, f'Added {qty} kg to {p.name}. New stock: {p.stock_quantity} kg')
    return redirect(f'/inventory/?store_id={store.id}' if profile.is_area_manager else 'inventory')


@login_required
@require_profile
def product_delete(request, pid):
    profile = get_profile(request.user)

    if profile.is_superadmin:
        p = get_object_or_404(Product, id=pid)
    elif profile.is_area_manager and not profile.store:
        p = get_object_or_404(Product, id=pid)
        has_access = AreaManagerStore.objects.filter(
            manager=profile, store=p.store
        ).exists()
        if not has_access:
            messages.error(request, 'Access denied.')
            return redirect('inventory')
    else:
        p = get_object_or_404(Product, id=pid, store=profile.store)

    store_id = p.store_id
    p.is_active = False
    p.save()
    messages.success(request, f'"{p.name}" removed.')
    return redirect(f'/inventory/?store_id={store_id}' if profile.is_area_manager else 'inventory')


@login_required
@require_profile
def stock_log(request):
    profile = get_profile(request.user)
    store   = profile.store
    if not store:
        return redirect('dashboard')
    logs = StockLog.objects.filter(store=store).select_related('product', 'created_by')[:200]
    return render(request, 'pos/stock_log.html', {'logs': logs, 'store': store, 'profile': profile})


# ══════════════════════════════════════════════════════════════════════════════
#  REPORTS  (owner + superadmin)
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def reports(request):
    profile = get_profile(request.user)
    if profile.is_staff_role:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    store   = profile.store
    from_date_str = request.GET.get('from_date', '')
    to_date_str = request.GET.get('to_date', '')
    
    try:
        from_date = date.fromisoformat(from_date_str) if from_date_str else date.today()
    except ValueError:
        from_date = date.today()
        
    try:
        to_date = date.fromisoformat(to_date_str) if to_date_str else from_date
    except ValueError:
        to_date = from_date

    r_start = date_range(from_date)[0]
    r_end = date_range(to_date)[1]

    qs_filter = {'sale__created_at__range': (r_start, r_end)}
    if store:
        qs_filter['sale__store'] = store
    items_qs = SaleItem.objects.filter(**qs_filter)
    
    product_summary = list(items_qs.values('product_name').annotate(
        qty=Sum('quantity'),
        sales=Sum('total_amount'),
        cost=Sum('total_cost'),
        profit=Sum('profit')
    ).order_by('-qty'))

    agg = items_qs.aggregate(
        total_sales=Sum('total_amount'), total_cost=Sum('total_cost'), total_profit=Sum('profit'))

    sale_filter = {'created_at__range': (r_start, r_end)}
    if store:
        sale_filter['store'] = store
    
    sales = list(Sale.objects.filter(**sale_filter).prefetch_related('items').select_related('store')[:200])
    
    pay_breakdown  = list(Sale.objects.filter(**sale_filter).values('payment_mode').annotate(
        count=Count('id'), total=Sum('grand_total')).order_by('-total'))
    type_breakdown = list(Sale.objects.filter(**sale_filter).values('bill_type').annotate(
        count=Count('id'), total=Sum('grand_total')).order_by('-total'))

    expenses_filter = {'date__range': (from_date, to_date)}
    if store:
        expenses_filter['store'] = store
    
    expenses = list(Expense.objects.filter(**expenses_filter)[:100])
    total_expense = float(Expense.objects.filter(**expenses_filter).aggregate(t=Sum('amount'))['t'] or 0)

    total_sales   = float(agg['total_sales']  or 0)
    total_cost    = float(agg['total_cost']   or 0)
    total_profit  = float(agg['total_profit'] or 0)
    net_profit    = total_profit - total_expense

    ctx = {
        'profile':         profile,
        'store':           store,
        'from_date':       from_date,
        'to_date':         to_date,
        'sales':           sales,
        'product_summary': product_summary,
        'total_sales':     total_sales,
        'total_cost':      total_cost,
        'total_profit':    total_profit,
        'pay_breakdown':   pay_breakdown,
        'type_breakdown':  type_breakdown,
        'expenses':        expenses,
        'total_expense':   total_expense,
        'net_profit':      net_profit,
    }
    return render(request, 'pos/reports.html', ctx)


@login_required
@require_profile
def export_excel(request):
    profile = get_profile(request.user)
    if profile.is_staff_role:
        return HttpResponse('Access denied', status=403)
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        return HttpResponse('openpyxl not installed.', status=500)

    store = profile.store
    from_date_str = request.GET.get('from_date', '')
    to_date_str = request.GET.get('to_date', '')
    
    try:
        from_date = date.fromisoformat(from_date_str) if from_date_str else date.today()
    except ValueError:
        from_date = date.today()
        
    try:
        to_date = date.fromisoformat(to_date_str) if to_date_str else from_date
    except ValueError:
        to_date = from_date

    r_start = date_range(from_date)[0]
    r_end = date_range(to_date)[1]
    qs_filter = {'sale__created_at__range': (r_start, r_end)}
    if store:
        qs_filter['sale__store'] = store
    items = SaleItem.objects.filter(**qs_filter).select_related('sale', 'sale__store').order_by('sale__created_at')

    wb  = openpyxl.Workbook()
    ws  = wb.active
    date_label = from_date.isoformat() if from_date == to_date else f"{from_date.isoformat()} to {to_date.isoformat()}"
    ws.title = f"Sales {date_label}"[:31]

    hdr_font  = Font(bold=True, color='FFFFFF', size=11)
    hdr_fill  = PatternFill('solid', fgColor='0077B6')
    ctr       = Alignment(horizontal='center', vertical='center')
    thin      = Side(style='thin', color='CCCCCC')
    bdr       = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells('A1:J1')
    ws['A1'] = 'OCEANWAVES SEA FOODS — Sales Report'
    ws['A1'].font = Font(bold=True, size=14, color='0077B6')
    ws['A1'].alignment = Alignment(horizontal='center')
    ws.merge_cells('A2:J2')
    ws['A2'] = f'Date: {date_label}'
    ws['A2'].alignment = Alignment(horizontal='center')
    ws['A2'].font = Font(italic=True)
    
    ws.merge_cells('A3:J3')
    ws['A3'] = 'OCEANWAVES SEA FOODS is part of OCEANWAVES VICTUALS PRIVATE LIMITED.'
    ws['A3'].alignment = Alignment(horizontal='center')
    ws['A3'].font = Font(size=9, italic=True)

    headers = ['Store', 'Bill No', 'Bill Type', 'Product', 'Qty (kg)', 'Mode',
               'CP/kg (₹)', 'SP/kg (₹)', 'Total (₹)', 'Profit (₹)']
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.font, cell.fill, cell.alignment, cell.border = hdr_font, hdr_fill, ctr, bdr

    row = 5
    for item in items:
        vals = [
            item.sale.store.name, item.sale.bill_number,
            item.sale.get_bill_type_display(), item.product_name,
            float(item.quantity), item.sale.get_payment_mode_display(),
            float(item.cost_price), float(item.selling_price),
            float(item.total_amount), float(item.profit),
        ]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=row, column=c, value=v)
            cell.border = bdr
            cell.alignment = Alignment(horizontal='right' if c > 4 else 'left')
        row += 1

    totals = items.aggregate(s=Sum('total_amount'), p=Sum('profit'))
    tf = PatternFill('solid', fgColor='E9F5FF')
    ws.cell(row=row, column=1, value='TOTALS').font = Font(bold=True)
    ws.cell(row=row, column=1).fill = tf
    ws.cell(row=row, column=9, value=float(totals['s'] or 0)).font = Font(bold=True)
    ws.cell(row=row, column=9).fill = tf
    ws.cell(row=row, column=10, value=float(totals['p'] or 0)).font = Font(bold=True)
    ws.cell(row=row, column=10).fill = tf

    for i, w in enumerate([18,16,12,22,10,10,12,12,14,12], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="OceanWaves_Sales_{date_label}.xlsx"'
    wb.save(resp)
    return resp



# ══════════════════════════════════════════════════════════════════════════════
#  EXPENSES  (add / delete / PDF upload)
# ══════════════════════════════════════════════════════════════════════════════
@require_POST
@login_required
@require_profile
def expense_add(request):
    profile = get_profile(request.user)
    if not profile.is_owner:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
        
    store = profile.store
    if profile.is_superadmin:
        store_id = request.POST.get('store_id')
        if not store_id:
            messages.error(request, 'Store selection is required for Super Admin.')
            return redirect('expenses_page')
        store = get_object_or_404(Store, id=store_id)
    
    exp = Expense(
        store=store,
        category=request.POST.get('category', 'OTHER'),
        description=request.POST.get('description', '').strip(),
        amount=request.POST.get('amount', 0),
        date=request.POST.get('date', date.today()),
        created_by=request.user,
    )
    
    bill = request.FILES.get('bill_pdf')
    if bill:
        import magic
        file_magic = magic.from_buffer(bill.read(2048), mime=True)
        bill.seek(0)
        
        allowed_mimes = ['application/pdf', 'image/jpeg', 'image/png']
        if file_magic not in allowed_mimes:
            messages.error(request, 'Invalid file type. Only PDF, JPG, PNG are allowed.')
            return redirect('expenses_page')
            
        exp.bill_pdf = bill
        
    exp.save()
    messages.success(request, 'Expense recorded.')
    return redirect('expenses_page')


@require_POST
@login_required
@require_profile
def expense_delete(request, expense_id):
    profile = get_profile(request.user)
    if not profile.is_owner:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
    exp = get_object_or_404(Expense, id=expense_id, store=profile.store)
    import os
    if exp.bill_pdf and hasattr(exp.bill_pdf, 'path'):
        try:
            if os.path.exists(exp.bill_pdf.path):
                os.remove(exp.bill_pdf.path)
        except Exception:
            pass
            
    from .audit import log_event
    log_event(request, 'EXPENSE_DELETED', f'expense={exp.id} amount={exp.amount}')
    exp.delete()
    messages.success(request, 'Expense deleted.')
    return redirect('expenses_page')


@login_required
@require_profile
def expenses_page(request):
    profile = get_profile(request.user)
    if not profile.is_owner:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')
        
    all_stores = None
    store_filter = request.GET.get('store_filter')

    if profile.is_superadmin:
        all_stores = Store.objects.filter(is_active=True).order_by('name')
        if store_filter and store_filter.isdigit():
            expenses = Expense.objects.filter(store_id=store_filter)
        else:
            expenses = Expense.objects.all()
    else:
        expenses = Expense.objects.filter(store=profile.store)

    report_date_str = request.GET.get('date', '')
    try:
        report_date = date.fromisoformat(report_date_str) if report_date_str else date.today()
    except ValueError:
        report_date = date.today()
        
    total        = expenses.aggregate(t=Sum('amount'))['t'] or 0
    day_expenses = expenses.filter(date=report_date)
    day_total    = day_expenses.aggregate(t=Sum('amount'))['t'] or 0
    
    return render(request, 'pos/expenses.html', {
        'profile':     profile, 
        'all_stores':  all_stores,
        'store_filter': store_filter,
        'expenses':    expenses.order_by('-date', '-created_at')[:100],
        'day_expenses': day_expenses,
        'report_date': report_date,
        'total':       total,
        'day_total':   day_total,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  EMPLOYEE MANAGER
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def employee_list(request):
    profile = get_profile(request.user)
    if not (profile.is_owner or profile.is_superadmin):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    all_stores = None
    store      = None
    store_id   = None
    if profile.is_superadmin:
        all_stores = Store.objects.filter(is_active=True).order_by('name')
        store_id   = request.GET.get('store_id')
        if store_id and store_id.isdigit():
            store = get_object_or_404(Store, id=store_id)
    else:
        store = profile.store

    if store:
        employees = Employee.objects.filter(store=store).order_by('is_active', 'full_name')
    elif profile.is_superadmin:
        if store_id == 'none':
            employees = Employee.objects.filter(store__isnull=True).order_by('is_active', 'full_name')
        else:
            # All stores view
            employees = Employee.objects.all().order_by('store__name', 'is_active', 'full_name')
    else:
        employees = Employee.objects.none()

    active_count   = employees.filter(is_active=True).count()
    inactive_count = employees.filter(is_active=False).count()
    total_payroll  = employees.filter(is_active=True).aggregate(t=Sum('basic_salary'))['t'] or 0

    return render(request, 'pos/employees.html', {
        'profile'       : profile,
        'store'         : store,
        'all_stores'    : all_stores,
        'selected_store_id': store_id,
        'employees'     : employees,
        'total_payroll' : total_payroll,
        'active_count'  : active_count,
        'inactive_count': inactive_count,
    })


@login_required
@require_profile
def employee_add(request):
    profile = get_profile(request.user)
    if not (profile.is_owner or profile.is_superadmin):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    # Superadmin can specify a store via POST; owner always uses their own store
    if profile.is_superadmin:
        store_id = request.POST.get('store_id') or request.GET.get('store_id')
        store = get_object_or_404(Store, id=store_id) if store_id else Store.objects.filter(is_active=True).first()
    else:
        store = profile.store

    if not store:
        messages.error(request, 'No store found. Please specify a store.')
        return redirect('employee_list')

    if request.method == 'POST':
        last   = Employee.objects.filter(store=store).order_by('-id').first()
        num    = (last.id + 1) if last else 1
        emp_id = f"EMP{store.id}{str(num).zfill(4)}"
        Employee.objects.create(
            store=store, employee_id=emp_id,
            full_name=request.POST.get('full_name', '').strip(),
            phone=request.POST.get('phone', '').strip(),
            email=request.POST.get('email', '').strip(),
            designation=request.POST.get('designation', '').strip(),
            employment_type=request.POST.get('employment_type', 'FULLTIME'),
            pay_cycle=request.POST.get('pay_cycle', 'MONTHLY'),
            basic_salary=request.POST.get('basic_salary', 0) or 0,
            allowances=request.POST.get('allowances', 0) or 0,
            deductions=request.POST.get('deductions', 0) or 0,
            date_joined=request.POST.get('date_joined', date.today()) or date.today(),
            notes=request.POST.get('notes', '').strip(),
            created_by=request.user,
        )
        messages.success(request, 'Employee added.')
    return redirect('employee_list')


@login_required
@require_profile
def employee_edit(request, emp_id):
    profile = get_profile(request.user)
    if not (profile.is_owner or profile.is_superadmin):
        return redirect('dashboard')
    emp = get_object_or_404(Employee, id=emp_id)
    if profile.is_owner and not profile.is_superadmin and emp.store != profile.store:
        messages.error(request, 'Access denied.')
        return redirect('employee_list')
    if request.method == 'POST':
        emp.full_name       = request.POST.get('full_name', emp.full_name).strip()
        emp.phone           = request.POST.get('phone', emp.phone).strip()
        emp.email           = request.POST.get('email', emp.email).strip()
        emp.designation     = request.POST.get('designation', emp.designation).strip()
        emp.employment_type = request.POST.get('employment_type', emp.employment_type)
        emp.pay_cycle       = request.POST.get('pay_cycle', emp.pay_cycle)
        emp.basic_salary    = request.POST.get('basic_salary', emp.basic_salary)
        emp.allowances      = request.POST.get('allowances', emp.allowances)
        emp.deductions      = request.POST.get('deductions', emp.deductions)
        emp.is_active       = request.POST.get('is_active') == 'on'
        emp.notes           = request.POST.get('notes', emp.notes).strip()
        emp.save()
        messages.success(request, f'"{emp.full_name}" updated.')
    return redirect('employee_list')


@login_required
@require_profile
def employee_delete(request, emp_id):
    profile = get_profile(request.user)
    if not (profile.is_owner or profile.is_superadmin):
        return redirect('dashboard')
    emp = get_object_or_404(Employee, id=emp_id)
    # Owners can only deactivate their own store's employees; superadmin can deactivate any
    if profile.is_owner and not profile.is_superadmin and emp.store != profile.store:
        messages.error(request, 'Access denied.')
        return redirect('employee_list')
    emp.is_active = False
    emp.save()
    messages.success(request, f'"{emp.full_name}" deactivated.')
    return redirect('employee_list')


@login_required
@require_profile
def employee_hard_delete(request, emp_id):
    """Permanently delete an inactive employee record."""
    profile = get_profile(request.user)
    if not (profile.is_owner or profile.is_superadmin):
        return redirect('dashboard')
    emp = get_object_or_404(Employee, id=emp_id)
    if profile.is_owner and not profile.is_superadmin and emp.store != profile.store:
        messages.error(request, 'Access denied.')
        return redirect('employee_list')
    if emp.is_active:
        messages.error(request, 'Cannot permanently delete an active employee. Deactivate first.')
        return redirect('employee_list')
    name = emp.full_name
    emp.delete()
    messages.success(request, f'Employee "{name}" permanently deleted.')
    return redirect('employee_list')


@login_required
@require_profile
def employee_detail(request, emp_id):
    profile  = get_profile(request.user)
    if not (profile.is_owner or profile.is_superadmin):
        return redirect('dashboard')
    emp = get_object_or_404(Employee, id=emp_id)
    if profile.is_owner and not profile.is_superadmin and emp.store != profile.store:
        messages.error(request, 'Access denied.')
        return redirect('employee_list')
    payslips = PaySlip.objects.filter(employee=emp).order_by('-year', '-month')
    return render(request, 'pos/employee_detail.html', {
        'profile': profile, 'emp': emp,
        'payslips': payslips, 'store': emp.store,
    })


@login_required
@require_profile
def payslip_generate(request, emp_id):
    profile = get_profile(request.user)
    if not profile.is_owner:
        return redirect('dashboard')
    # Superadmin has no store; look up by id only, then verify ownership for regular owners
    if profile.is_superadmin:
        emp = get_object_or_404(Employee, id=emp_id)
    else:
        emp = get_object_or_404(Employee, id=emp_id, store=profile.store)
    if request.method == 'POST':
        month = int(request.POST.get('month'))
        year  = int(request.POST.get('year'))
        if PaySlip.objects.filter(employee=emp, month=month, year=year).exists():
            messages.error(request, 'Payslip for this month already exists.')
        else:
            def to_dec(val, fallback=0):
                try:
                    return decimal.Decimal(str(val)).quantize(decimal.Decimal('0.01'))
                except Exception:
                    return decimal.Decimal(str(fallback)).quantize(decimal.Decimal('0.01'))
            PaySlip.objects.create(
                employee=emp, store=emp.store,
                month=month, year=year,
                basic_salary=to_dec(request.POST.get('basic_salary'), emp.basic_salary),
                allowances=to_dec(request.POST.get('allowances'),     emp.allowances),
                deductions=to_dec(request.POST.get('deductions'),     emp.deductions),
                bonus=to_dec(request.POST.get('bonus'), 0),
                status=request.POST.get('status', 'PENDING'),
                payment_date=request.POST.get('payment_date') or None,
                payment_mode=request.POST.get('payment_mode', '').strip(),
                notes=request.POST.get('notes', '').strip(),
                created_by=request.user,
            )
            messages.success(request, f'Payslip generated for {emp.full_name}.')
    return redirect('employee_detail', emp_id=emp_id)


@login_required
@require_profile
def payslip_mark_paid(request, slip_id):
    profile = get_profile(request.user)
    if not profile.is_owner:
        return redirect('dashboard')
    slip = get_object_or_404(PaySlip, id=slip_id) if profile.is_superadmin else get_object_or_404(PaySlip, id=slip_id, store=profile.store)
    slip.status       = 'PAID'
    slip.payment_date = date.today()
    slip.payment_mode = request.POST.get('payment_mode', 'CASH')
    slip.save()
    messages.success(request, 'Payslip marked as paid.')
    return redirect('employee_detail', emp_id=slip.employee.id)


@login_required
@require_profile
def payslip_delete(request, slip_id):
    profile = get_profile(request.user)
    if not profile.is_owner:
        return redirect('dashboard')
    slip   = get_object_or_404(PaySlip, id=slip_id) if profile.is_superadmin else get_object_or_404(PaySlip, id=slip_id, store=profile.store)
    emp_id = slip.employee.id
    slip.delete()
    messages.success(request, 'Payslip deleted.')
    return redirect('employee_detail', emp_id=emp_id)


@login_required
@require_profile
def payslip_print(request, slip_id):
    profile = get_profile(request.user)
    slip    = get_object_or_404(PaySlip, id=slip_id) if profile.is_superadmin else get_object_or_404(PaySlip, id=slip_id, store=profile.store)
    return render(request, 'pos/payslip_print.html', {'slip': slip})


# ══════════════════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def product_api(request, pid):
    profile = get_profile(request.user)
    p = get_object_or_404(Product, id=pid, store=profile.store, is_active=True)
    return JsonResponse({
        'id': p.id, 'name': p.name,
        'retail_price':    str(p.retail_price),
        'wholesale_price': str(p.wholesale_price),
        'stock_quantity':  str(p.stock_quantity),
        'category':        p.category,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  AREA MANAGER MANAGEMENT  (superadmin only)
# ══════════════════════════════════════════════════════════════════════════════
@login_required
@require_profile
def area_manager_list(request):
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    managers  = UserProfile.objects.filter(role='AREAMANAGER').select_related('user').prefetch_related('managed_stores__store')
    stores    = Store.objects.filter(is_active=True)
    all_links = AreaManagerStore.objects.select_related('manager__user', 'store').order_by('store__name')
    return render(request, 'pos/area_managers.html', {
        'managers':  managers,
        'stores':    stores,
        'all_links': all_links,
        'profile':   profile,
    })


@login_required
@require_profile
def area_manager_assign(request):
    """Assign or remove a store from an area manager."""
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return redirect('dashboard')

    if request.method == 'POST':
        action     = request.POST.get('action')          # 'assign' or 'remove'
        manager_id = request.POST.get('manager_id')
        store_ids  = request.POST.getlist('store_ids')   # for assign: list of stores

        manager_profile = get_object_or_404(UserProfile, id=manager_id, role='AREAMANAGER')

        if action == 'assign':
            added = 0
            for sid in store_ids:
                store = get_object_or_404(Store, id=sid)
                _, created = AreaManagerStore.objects.get_or_create(
                    manager=manager_profile, store=store,
                    defaults={'assigned_by': request.user}
                )
                if created:
                    added += 1
            messages.success(request, f'Assigned {added} store(s) to {manager_profile.user.username}.')

        elif action == 'remove':
            link_id = request.POST.get('link_id')
            AreaManagerStore.objects.filter(id=link_id).delete()
            messages.success(request, 'Store assignment removed.')

        elif action == 'remove_all':
            AreaManagerStore.objects.filter(manager=manager_profile).delete()
            messages.success(request, f'All store assignments removed for {manager_profile.user.username}.')

    return redirect('area_manager_list')


# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  WHOLESALE PIN APPROVAL  (fast, zero-cost, works offline)
# ══════════════════════════════════════════════════════════════════════════════

def _hash_pin(raw_pin):
    from django.contrib.auth.hashers import make_password
    return make_password(raw_pin)

def _check_pin(profile, entered_pin):
    from django.contrib.auth.hashers import check_password
    if not profile.approval_pin:
        return False, 'PIN not set for this manager.'
    return check_password(str(entered_pin), profile.approval_pin), ''


@login_required
@require_profile
def wholesale_managers(request):
    """
    AJAX: Return list of Area Managers (with PINs set) for this store.
    Called when cashier opens the approval dialog — no bill data yet.
    """
    profile = get_profile(request.user)
    store   = profile.store
    if not store:
        return JsonResponse({'success': False, 'error': 'Not assigned to a store.'})

    am_links = AreaManagerStore.objects.filter(
        store=store, manager__is_active=True
    ).select_related('manager__user')

    if not am_links.exists():
        return JsonResponse({
            'success': False,
            'no_managers': True,
            'error': 'No Area Manager assigned to this store. Contact admin.'
        })

    managers_ready = []
    managers_no_email = []
    for link in am_links:
        am   = link.manager
        name = am.user.get_full_name() or am.user.username
        if am.user.email:
            managers_ready.append({'id': am.id, 'name': name})
        else:
            managers_no_email.append(name)

    return JsonResponse({
        'success':         True,
        'managers':        managers_ready,
        'managers_no_pin': managers_no_email,
    })


@login_required
@require_profile
def wholesale_request_otp(request):
    """
    AJAX: Generate 6-digit OTP, send it to the chosen manager, cache it.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST only'}, status=405)

    profile = get_profile(request.user)
    store   = profile.store
    if not store:
        return JsonResponse({'success': False, 'error': 'Not assigned to a store.'})

    try:
        data       = json.loads(request.body)
        manager_id = data.get('manager_id')

        if not manager_id:
            return JsonResponse({'success': False, 'error': 'Manager ID required.'})

        link = AreaManagerStore.objects.filter(
            store=store, manager__id=manager_id, manager__is_active=True
        ).select_related('manager__user').first()

        if not link:
            return JsonResponse({'success': False, 'error': 'Manager not found or not assigned to this store.'})

        am = link.manager
        email = am.user.email
        if not email:
            return JsonResponse({'success': False, 'error': 'Selected Manager has no email address configured.'})
            
        import random, string
        from django.core.mail import send_mail
        from django.conf import settings
        from django.core.cache import cache
        
        otp = ''.join(random.choices(string.digits, k=6))
        cache_key = f'ws_otp_{am.id}_{store.id}'
        cache.set(cache_key, otp, timeout=300) # 5 min

        # Surface OTP directly to AM Dashboard to bypass any SMTP failures
        am_dashboard_key = f'am_dashboard_otps_{am.id}'
        existing = cache.get(am_dashboard_key, [])
        import time, datetime
        now = time.time()
        existing = [o for o in existing if now - o['ts'] < 300]
        existing.insert(0, {
            'store_name': store.name,
            'time': datetime.datetime.now().strftime("%I:%M %p"),
            'code': otp,
            'ts': now
        })
        cache.set(am_dashboard_key, existing, timeout=300)

        requestor_name = request.user.get_full_name() or request.user.username
        store_info = store.name
        if store.address:
            store_info += f", {store.address}"
        if store.phone:
            store_info += f" | Ph: {store.phone}"

        subject = f"[ACTION REQUIRED] Wholesale Approval OTP — {store.name}"
        message = (
            f"Hello {am.user.first_name or am.user.username},\n\n"
            f"A wholesale bill is awaiting your approval.\n\n"
            f"{'─' * 40}\n"
            f"  Store     : {store.name}\n"
            f"  Address   : {store.address or 'N/A'}\n"
            f"  Requested by: {requestor_name}\n"
            f"  Time      : {datetime.datetime.now().strftime('%d %b %Y, %I:%M %p')}\n"
            f"{'─' * 40}\n\n"
            f"Your OTP: {otp}\n\n"
            f"This OTP is valid for 5 minutes.\n"
            f"Do NOT share this OTP with anyone.\n\n"
            f"— OCEANWAVES POS System\n"
        )

        from django.core.mail import send_mail
        try:
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
        except Exception:
            pass

        return JsonResponse({'success': True, 'msg': f'OTP sent to Manager & visible on Manager Dashboard'})
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_profile
def wholesale_verify_otp(request):
    """
    AJAX: Cashier submits bill data + chosen manager ID + entered OTP.
    Verifies OTP → saves bill → returns bill id for print.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST only'}, status=405)

    profile = get_profile(request.user)
    store   = profile.store
    if not store:
        return JsonResponse({'success': False, 'error': 'Not assigned to a store.'})

    try:
        data       = json.loads(request.body)
        manager_id = data.get('manager_id')
        entered_otp = str(data.get('pin', '')).strip()

        if not manager_id or not entered_otp:
            return JsonResponse({'success': False, 'error': 'Manager and OTP required.'})

        # Verify manager is assigned to this store
        link = AreaManagerStore.objects.filter(
            store=store, manager__id=manager_id, manager__is_active=True
        ).select_related('manager__user').first()

        if not link:
            return JsonResponse({'success': False, 'error': 'Manager not found.'})

        am = link.manager
        
        from django.core.cache import cache
        cache_key = f'ws_otp_{am.id}_{store.id}'
        cached_otp = cache.get(cache_key)
        
        if not cached_otp:
            return JsonResponse({
                'success': False,
                'wrong_pin': True,
                'error': 'OTP expired or not requested. Please request a new OTP.'
            })
            
        if cached_otp != entered_otp:
            return JsonResponse({
                'success': False,
                'wrong_pin': True,
                'error': f'Invalid OTP. Please check the email sent to the manager.'
            })

        # ── OTP correct — clear cache, save bill ──
        cache.delete(cache_key)

        items_data  = data.get('items', [])
        bill_type   = data.get('bill_type', 'WHOLESALE')
        payment     = data.get('payment_mode', 'CASH')
        gst_rate    = decimal.Decimal(str(data.get('gst_rate', 0)))
        discount    = decimal.Decimal(str(data.get('discount', 0)))

        # Re-validate stock
        validated = []
        for it in items_data:
            product = get_object_or_404(Product, id=it['product_id'], store=store)
            qty     = decimal.Decimal(str(it['quantity']))
            sp      = decimal.Decimal(str(it['selling_price']))
            if product.stock_quantity < qty:
                return JsonResponse({'success': False,
                    'error': f'Insufficient stock: {product.name} only has {product.stock_quantity} kg.'})
            validated.append((product, qty, sp))

        # Build Sale
        sale = Sale(
            store=store, bill_type=bill_type, payment_mode=payment,
            gst_rate=gst_rate, discount=discount, created_by=request.user
        )
        cname = data.get('customer_name',  '').strip()
        sale.customer_name    = cname
        sale.customer_phone   = data.get('customer_phone',   '').strip()
        sale.customer_gst     = data.get('customer_gst',     '').strip()
        sale.customer_address = data.get('customer_address', '').strip()

        wc = None
        from .models import WholesaleCustomer, CreditRecord
        if cname:
            # Lookup by Name OR Customer Code
            wc = WholesaleCustomer.objects.filter(
                Q(name__iexact=cname) | Q(customer_code__iexact=cname)
            ).first()
            if payment == 'CREDIT':
                if not wc:
                    wc = WholesaleCustomer.objects.create(
                        name=cname, phone=sale.customer_phone, gst=sale.customer_gst,
                        address=sale.customer_address,
                        is_credit_enabled=True, credit_duration_days=7, created_by=request.user
                    )
                else:
                    if not wc.is_credit_enabled:
                        return JsonResponse({'success': False, 'error': f'Credit is disabled for {cname}.'})
                    # if wc.has_unpaid_credit:
                    #     return JsonResponse({'success': False, 'error': f'{cname} has an outstanding credit balance of ₹{wc.balance}. Please settle it before making new credit sales.'})
            sale.wholesale_customer = wc

        subtotal      = sum(q * sp for _, q, sp in validated)
        sale.subtotal = subtotal - discount

        if gst_rate > 0:
            half             = gst_rate / 2
            sale.cgst_amount = (sale.subtotal * half / 100).quantize(decimal.Decimal('0.01'))
            sale.sgst_amount = sale.cgst_amount
            sale.total_gst   = sale.cgst_amount + sale.sgst_amount
            sale.grand_total = sale.subtotal + sale.total_gst
        else:
            sale.grand_total = sale.subtotal

        sale.save()
        
        if payment == 'CREDIT' and wc:
            from datetime import timedelta
            cr = CreditRecord.objects.create(
                customer=wc, sale=sale,
                due_date=sale.created_at.date() + timedelta(days=wc.credit_duration_days)
            )
            # Force created_at to match the backdated sale timestamp
            CreditRecord.objects.filter(id=cr.id).update(created_at=sale.created_at)

        # Create SaleItems + deduct stock + log
        for product, qty, sp in validated:
            cp = product.cost_price
            SaleItem.objects.create(
                sale=sale, product=product, product_name=product.name,
                quantity=qty, cost_price=cp, selling_price=sp,
                total_amount=qty * sp, total_cost=qty * cp, profit=qty * (sp - cp)
            )
            product.stock_quantity -= qty
            product.save(update_fields=['stock_quantity'])
            StockLog.objects.create(
                store=store, product=product, movement='OUT',
                quantity=qty, balance=product.stock_quantity,
                reference=sale.bill_number, created_by=request.user
            )

        # Write approval audit record
        items_summary = ', '.join(
            f"{v[0].name} {v[1]}kg" for v in validated[:4]
        ) + (f' +{len(validated)-4} more' if len(validated) > 4 else '')

        WholesaleApproval.objects.create(
            store=store,
            area_manager=am,
            sale=sale,
            bill_snapshot={
                'items_summary': items_summary,
                'grand_total':   str(sale.grand_total),
                'bill_type':     bill_type,
                'customer':      sale.customer_name,
            },
            approved_by_name=am.user.get_full_name() or am.user.username,
            created_by=request.user,
        )

        # Build WhatsApp URL for wholesale bill
        import urllib.parse
        items_wa = "\n".join(
            f"  \u2022 {v[0].name}: {v[1]}kg \u00d7 \u20b9{v[2]} = \u20b9{v[1]*v[2]:.2f}"
            for v in validated
        )
        wa_msg = (
            f"\U0001f30a *OCEANWAVES SEA FOODS*\n"
            f"\U0001f4cd {store.name}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            f"\U0001f9fe Bill No: *{sale.bill_number}*\n"
            f"\U0001f4c5 Date: {sale.created_at.strftime('%d/%m/%Y %I:%M %p')}\n"
            f"\U0001f3db\ufe0f *GST TAX INVOICE*\n"
            f"\U0001f4b3 Payment: {sale.get_payment_mode_display()}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            f"{items_wa}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        )
        if sale.discount > 0:
            wa_msg += f"\U0001f3f7\ufe0f Discount: -\u20b9{sale.discount:.2f}\n"
        if sale.total_gst > 0:
            wa_msg += f"GST ({sale.gst_rate}%): \u20b9{sale.total_gst:.2f}\n"
        wa_msg += f"\U0001f4b0 *TOTAL: \u20b9{sale.grand_total:.2f}*\n"
        wa_msg += f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        wa_msg += f"\u2728 Thank you for your business!\nFresh Seafood Every Day \U0001f41f"

        customer_phone = sale.customer_phone.strip().lstrip('+').replace(' ', '').replace('-', '')
        store_wa = (store.whatsapp_number or '').strip().lstrip('+').replace(' ', '').replace('-', '')
        wa_phone = customer_phone if customer_phone else store_wa
        encoded_msg = urllib.parse.quote(wa_msg)
        whatsapp_url = f"https://wa.me/{wa_phone}?text={encoded_msg}" if wa_phone else None

        return JsonResponse({
            'success':       True,
            'bill_id':       sale.id,
            'bill_number':   sale.bill_number,
            'approved_by':   am.user.get_full_name() or am.user.username,
            'whatsapp_url':  whatsapp_url,
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ── PIN Management (superadmin sets PINs for Area Managers) ──────────────────
@login_required
@require_profile
def set_manager_pin(request):
    """Superadmin sets or resets an Area Manager's approval PIN."""
    profile = get_profile(request.user)
    if not profile.is_superadmin:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST only'}, status=405)

    try:
        data       = json.loads(request.body)
        manager_id = data.get('manager_id')
        new_pin    = str(data.get('pin', '')).strip()

        if not new_pin.isdigit() or not (4 <= len(new_pin) <= 6):
            return JsonResponse({'success': False, 'error': 'PIN must be 4 to 6 digits.'})

        am = get_object_or_404(UserProfile, id=manager_id, role='AREAMANAGER')
        am.approval_pin = _hash_pin(new_pin)
        am.save(update_fields=['approval_pin'])

        return JsonResponse({
            'success': True,
            'message': f'PIN set for {am.user.get_full_name() or am.user.username}.'
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ── Area Manager Dashboard ───────────────────────────────────────────────────
@login_required
@require_profile
def am_dashboard(request):
    """Personal dashboard for Area Managers — shows their approval history and pending OTPs."""
    profile = get_profile(request.user)
    if not profile.is_area_manager:
        messages.error(request, 'Access denied. Area Manager role required.')
        return redirect('dashboard')

    # Approval history queryset (unsliced for counts)
    approvals_qs = WholesaleApproval.objects.select_related(
        'store', 'sale', 'created_by'
    ).filter(area_manager=profile).order_by('-created_at')

    # Stats — must be computed BEFORE slicing
    total_approved = approvals_qs.count()
    today_approved = approvals_qs.filter(created_at__date=timezone.now().date()).count()
    stores_managed = profile.managed_stores.select_related('store').count()

    # Pending OTPs still in cache
    from django.core.cache import cache
    pending_otps = cache.get(f'am_dashboard_otps_{profile.id}', [])

    # Now slice for display
    approvals = approvals_qs[:100]

    return render(request, 'pos/am_dashboard.html', {
        'profile':       profile,
        'approvals':     approvals,
        'pending_otps':  pending_otps,
        'total_approved': total_approved,
        'today_approved': today_approved,
        'stores_managed': stores_managed,
        'unread_notifications': Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:10]
    })


# ── Approval history view ────────────────────────────────────────────────────
@login_required
@require_profile
def approval_history(request):
    """Show wholesale approval log for owner/superadmin."""
    profile = get_profile(request.user)
    if profile.is_staff_role:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    store = profile.store
    qs    = WholesaleApproval.objects.select_related(
        'area_manager__user', 'store', 'sale', 'created_by'
    )
    if store:
        qs = qs.filter(store=store)
    qs = qs[:200]

    return render(request, 'pos/approval_history.html', {
        'approvals': qs,
        'store':     store,
        'profile':   profile,
    })

# ─────────────────────────────────────────────────────────────────────────────
#  WHOLESALE CUSTOMERS & CREDITS
# ─────────────────────────────────────────────────────────────────────────────
@login_required
@require_profile
def wholesale_customers(request):
    profile = get_profile(request.user)
    from .models import Store, AreaManagerStore, WholesaleCustomer
    
    # Get accessible stores
    stores = Store.objects.filter(is_active=True).order_by('name')
    if not profile.is_superadmin:
        if profile.is_area_manager or profile.is_wholesale_exec:
            stores = Store.objects.filter(id__in=AreaManagerStore.objects.filter(manager=profile).values_list('store_id', flat=True)).order_by('name')
        elif profile.store:
            stores = stores.filter(id=profile.store.id)
        else:
            stores = Store.objects.none()

    customers = WholesaleCustomer.objects.all()
    
    store_filter = request.GET.get('store_filter')
    if store_filter and store_filter.isdigit():
        from django.db.models import Q
        customers = customers.filter(Q(store_id=store_filter) | Q(sales__store_id=store_filter)).distinct()
        
    customers = customers.order_by('-created_at')
    return render(request, 'pos/wholesale_customers.html', {
        'customers': customers, 
        'profile': profile,
        'stores': stores,
        'store_filter': store_filter
    })


@login_required
@require_profile
def wholesale_customer_add(request):
    from .models import WholesaleCustomer
    profile = get_profile(request.user)

        
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if WholesaleCustomer.objects.filter(name__iexact=name).exists():
            messages.error(request, 'Customer with this name already exists.')
        else:
            store = profile.store
            store_id = request.POST.get('store_id')
            if store_id:
                from .models import Store
                store = Store.objects.filter(id=store_id).first()
            else:
                store = None

            wc = WholesaleCustomer.objects.create(
                name=name,
                store=store,
                customer_code=request.POST.get('customer_code', '').strip(),
                phone=request.POST.get('phone', ''),
                email=request.POST.get('email', ''),
                gst=request.POST.get('gst', ''),
                credit_duration_days=int(request.POST.get('credit_duration_days', 7)),
                is_credit_enabled=request.POST.get('is_credit_enabled') == 'on',
                created_by=request.user
            )
            messages.success(request, f'Customer {wc.name} added with code {wc.customer_code}.')
    return redirect('wholesale_customers')

@login_required
@require_profile
def wholesale_customer_edit(request, cid):
    from .models import WholesaleCustomer
    profile = get_profile(request.user)

        
    c = get_object_or_404(WholesaleCustomer, id=cid)
    if request.method == 'POST':
        name = request.POST.get('name', c.name).strip()
        if WholesaleCustomer.objects.filter(name__iexact=name).exclude(id=cid).exists():
            messages.error(request, 'Another customer with this name already exists.')
            return redirect('wholesale_customers')
        c.name = name
        
        code = request.POST.get('customer_code', '').strip()
        if code and WholesaleCustomer.objects.filter(customer_code__iexact=code).exclude(id=cid).exists():
            messages.error(request, 'Another customer with this code already exists.')
            return redirect('wholesale_customers')
        c.customer_code = code or c.customer_code
        
        c.phone = request.POST.get('phone', c.phone)
        c.email = request.POST.get('email', c.email)
        c.gst   = request.POST.get('gst', c.gst)
        
        cd_days = request.POST.get('credit_duration_days')
        if cd_days and str(cd_days).isdigit():
            c.credit_duration_days = int(cd_days)
            
        c.is_credit_enabled = request.POST.get('is_credit_enabled') == 'on'
        
        store_id = request.POST.get('store_id')
        if store_id:
            from .models import Store
            c.store = Store.objects.filter(id=store_id).first()
        else:
            c.store = None

        c.save()
        messages.success(request, 'Customer updated.')
    return redirect('wholesale_customers')

@login_required
@require_profile
def wholesale_customer_delete(request, cid):
    from .models import WholesaleCustomer
    profile = get_profile(request.user)

        
    c = get_object_or_404(WholesaleCustomer, id=cid)
    
    # Check if they have outstanding balances before deleting
    if c.balance > 0:
        messages.error(request, f'Cannot delete customer {c.name} because they have an outstanding balance of ₹{c.balance}.')
        return redirect('wholesale_customers')
        
    if request.method == 'POST':
        try:
            c.delete()
            messages.success(request, f'Wholesale customer deleted successfully.')
        except Exception as e:
            messages.error(request, f'Error deleting customer: {str(e)}')
            
    return redirect('wholesale_customers')

@login_required
@require_profile
def credits_list(request):
    profile = get_profile(request.user)
    from .models import Store, AreaManagerStore, WholesaleCustomer
    
    # Summary of balances per customer
    customers = WholesaleCustomer.objects.filter(is_credit_enabled=True).prefetch_related('credit_records', 'payments')
    
    stores = Store.objects.filter(is_active=True).order_by('name')
    
    if profile.is_superadmin:
        pass
    elif profile.is_area_manager or profile.is_wholesale_exec:
        accessible_store_ids = AreaManagerStore.objects.filter(manager=profile).values_list('store_id', flat=True)
        stores = Store.objects.filter(id__in=accessible_store_ids).order_by('name')
        customers = customers.filter(
            Q(credit_records__sale__store_id__in=accessible_store_ids) | Q(store_id__in=accessible_store_ids)
        ).distinct()
    elif profile.store:
        stores = stores.filter(id=profile.store.id)
        customers = customers.filter(
            Q(credit_records__sale__store=profile.store) | Q(store=profile.store)
        ).distinct()
    else:
        stores = Store.objects.none()
        customers = WholesaleCustomer.objects.none()

    q_search = request.GET.get('q', '').strip()
    if q_search:
        customers = customers.filter(Q(name__icontains=q_search) | Q(phone__icontains=q_search))

    customer_summaries = []
    for c in customers:
        balance = c.balance
        if balance != 0 or c.credit_records.exists():
            customer_summaries.append({
                'customer': c,
                'last_credit': c.last_credit_date,
                'total_amount': c.total_credit_amount,
                'balance': balance,
            })

    customer_summaries.sort(key=lambda x: x['balance'], reverse=True)

    return render(request, 'pos/credits_list.html', {
        'summaries': customer_summaries,
        'stores': stores,
        'profile': profile,
        'q': q_search
    })


@login_required
@require_profile
def customer_credit_detail(request, customer_id):
    profile = get_profile(request.user)
    from .models import WholesaleCustomer, CreditRecord, CreditPayment
    customer = get_object_or_404(WholesaleCustomer, id=customer_id)
    
    records = list(customer.credit_records.select_related('sale').prefetch_related('sale__items').order_by('-created_at'))
    payments = list(customer.payments.order_by('-date', '-created_at'))
    
    ledger = []
    for r in records:
        ledger.append({
            'type': 'CREDIT',
            'date': r.created_at,
            'amount': r.total_due,
            'ref': r.sale.bill_number if r.sale else r.external_reference,
            'obj': r,
            'items': r.sale.items.all() if r.sale else []
        })
    for p in payments:
        import datetime
        from django.utils import timezone
        dt = timezone.make_aware(datetime.datetime.combine(p.date, datetime.time.min))
        ledger.append({
            'type': 'PAYMENT',
            'date': dt,
            'amount': p.amount,
            'ref': f"PMT-{p.id}",
            'obj': p,
            'items': []
        })
    
    # Sort ledger by date newest first
    ledger.sort(key=lambda x: x['date'], reverse=True)

    return render(request, 'pos/customer_credit_detail.html', {
        'customer': customer,
        'ledger': ledger,
        'profile': profile
    })


@login_required
@require_profile
@require_POST
def record_credit_payment(request, customer_id):
    from .models import WholesaleCustomer, CreditPayment
    customer = get_object_or_404(WholesaleCustomer, id=customer_id)
    amount = request.POST.get('amount')
    mode = request.POST.get('payment_mode', 'CASH')
    note = request.POST.get('note', '')
    
    payment_date_str = request.POST.get('payment_date')
    
    import datetime
    from django.utils import timezone
    pay_date = timezone.now().date()
    
    if payment_date_str:
        try:
            pay_date = datetime.datetime.strptime(payment_date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
            
    try:
        CreditPayment.objects.create(
            customer=customer,
            amount=amount,
            payment_mode=mode,
            note=note,
            date=pay_date,
            created_by=request.user
        )
        messages.success(request, f"Payment of ₹{amount} recorded for {customer.name} on {pay_date.strftime('%d-%m-%Y')}.")
    except Exception as e:
        messages.error(request, f"Error: {e}")
        
    return redirect('customer_credit_detail', customer_id=customer_id)


@login_required
@require_profile
def credit_add_external(request):
    profile = get_profile(request.user)
    from .models import WholesaleCustomer, CreditRecord
    import datetime

    if request.method == 'POST':
        customer_id = request.POST.get('customer')
        amount      = request.POST.get('amount')
        due_date    = request.POST.get('due_date')
        ref         = request.POST.get('reference', '').strip()
        
        if not customer_id or not amount or not due_date:
            messages.error(request, "Please fill all required fields.")
            return redirect('credit_add_external')
            
        customer = get_object_or_404(WholesaleCustomer, id=customer_id)
        
        try:
            amt = decimal.Decimal(str(amount))
            CreditRecord.objects.create(
                customer=customer,
                amount=amt,
                is_external=True,
                external_reference=ref,
                due_date=datetime.datetime.strptime(due_date, "%Y-%m-%d").date(),
                is_paid=False
            )
            messages.success(request, f"External credit of ₹{amt} added for {customer.name}.")
            return redirect('credits_list')
        except Exception as e:
            messages.error(request, f"Error saving credit: {e}")
            return redirect('credit_add_external')
            
    customers = WholesaleCustomer.objects.filter(is_credit_enabled=True)
    return render(request, 'pos/credit_add_external.html', {'customers': customers, 'profile': profile})


@require_POST
@login_required
@require_profile
def credit_pay(request, cid):
    from .models import AreaManagerStore
    profile = get_profile(request.user)
    record = get_object_or_404(CreditRecord, id=cid)
    
    can_access = False
    if profile.is_superadmin:
        can_access = True
    elif record.is_external or not record.sale:
        # External credit: check customer's registered store
        if profile.store and (record.customer.store == profile.store or not record.customer.store):
            can_access = True
        elif profile.is_area_manager or profile.is_wholesale_exec:
            accessible_store_ids = AreaManagerStore.objects.filter(manager=profile).values_list('store_id', flat=True)
            if record.customer.store_id in accessible_store_ids or not record.customer.store:
                can_access = True
    else:
        # POS Sale credit: check sale's store
        if profile.store and profile.store == record.sale.store:
            can_access = True
        elif profile.is_area_manager or profile.is_wholesale_exec:
            accessible_store_ids = AreaManagerStore.objects.filter(manager=profile).values_list('store_id', flat=True)
            if record.sale.store_id in accessible_store_ids:
                can_access = True
            
    if not can_access:
        messages.error(request, 'Access denied to this record.')
        from .audit import log_event
        log_event(request, 'IDOR_ATTEMPT', f'credit_pay record={cid}', level='WARNING')
        return redirect('credits_list')

    record.is_paid = True
    record.paid_on = timezone.now().date()
    record.save()
    messages.success(request, f'Credit for {record.customer.name} marked as paid.')
        
    return redirect('credits_list')

# ─────────────────────────────────────────────────────────────────────────────
#  SESSION MANAGEMENT & ALERTS
# ─────────────────────────────────────────────────────────────────────────────
from django.conf import settings
from .models import StoreSession, DailyStockSnapshot

@login_required
@require_profile
@require_POST
def toggle_store_session(request):
    profile = get_profile(request.user)
    if not profile.store:
        messages.error(request, 'Not assigned to a store.')
        return redirect('dashboard')
        
    store = profile.store
    action = request.POST.get('action')
    today = timezone.now().date()
    
    if action == 'open':
        session, created = StoreSession.objects.get_or_create(
            store=store, date=today,
            defaults={'opened_at': timezone.now(), 'opened_by': request.user}
        )
        if not created and not session.opened_at:
            session.opened_at = timezone.now()
            session.opened_by = request.user
            session.save()
        messages.success(request, f'{store.name} opened successfully.')
        
    elif action == 'close':
        session = StoreSession.objects.filter(store=store, date=today).first()
        if session and session.opened_at and not session.closed_at:
            session.closed_at = timezone.now()
            session.closed_by = request.user
            session.save()
            
            # --- Auto-calculate DailyStockSnapshot ---
            for p in store.products.filter(is_active=True):
                logs = StockLog.objects.filter(store=store, product=p, created_at__date=today)
                purchased = logs.filter(movement='IN').aggregate(t=Sum('quantity'))['t'] or 0
                sold      = logs.filter(movement='OUT').aggregate(t=Sum('quantity'))['t'] or 0
                
                yesterday_snap = DailyStockSnapshot.objects.filter(store=store, product=p, date__lt=today).order_by('-date').first()
                opening = yesterday_snap.closing_qty if yesterday_snap else (p.stock_quantity + sold - purchased)
                
                DailyStockSnapshot.objects.update_or_create(
                    store=store, product=p, date=today,
                    defaults={
                        'opening_qty': opening,
                        'purchased_qty': purchased,
                        'sold_qty': sold,
                        'closing_qty': p.stock_quantity
                    }
                )

            # TODO: re-add store-close admin email alert with better logic
            messages.success(request, f'{store.name} closed successfully.')
        else:
            messages.error(request, 'Store is not currently open.')
            
    return redirect('dashboard')


# ─────────────────────────────────────────────────────────────────────────────
#  STOCK REQUESTS & NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_profile
def stock_request_create(request):
    """Allows Store Owners to request stock for a product."""
    profile = get_profile(request.user)
    if request.method == 'POST':
        pid = request.POST.get('product_id')
        qty = decimal.Decimal(request.POST.get('requested_qty', 0))
        notes = request.POST.get('notes', '')
        
        product = get_object_or_404(Product, id=pid, store=profile.store)
        
        req = StockRequest.objects.create(
            store=profile.store,
            product=product,
            requested_qty=qty,
            requested_by=request.user,
            notes=notes
        )
        
        # Notify Area Managers
        notify_area_managers(
            store=profile.store,
            title="New Stock Request",
            message=f"{profile.store.name} requested {qty}kg of {product.name}.",
            level='INFO',
            link='/stock-requests/'
        )
        
        messages.success(request, f"Stock request for {product.name} submitted.")
    return redirect('inventory')


@login_required
@require_profile
def stock_request_list(request):
    """Lists stock requests based on the user's role."""
    profile = get_profile(request.user)
    if profile.is_superadmin:
        requests = StockRequest.objects.all()
    elif profile.role in ('AREAMANAGER', 'WHOLESALE_EXEC'):
        managed_store_ids = AreaManagerStore.objects.filter(manager__user=request.user).values_list('store_id', flat=True)
        requests = StockRequest.objects.filter(store_id__in=managed_store_ids)
    else:
        requests = StockRequest.objects.filter(store=profile.store)
    
    # Also get pending products for the 'Choose Product' modal if needed
    products = Product.objects.filter(store=profile.store, is_active=True)
    
    return render(request, 'pos/stock_requests.html', {
        'requests': requests,
        'profile': profile,
        'products': products
    })


@login_required
@require_profile
def stock_request_approve(request, rid):
    """Area Manager approves and records the quantity sent."""
    profile = get_profile(request.user)
    if not (profile.is_superadmin or profile.role in ('AREAMANAGER', 'WHOLESALE_EXEC')):
        messages.error(request, "Only Area Managers or Admins can approve stock requests.")
        return redirect('stock_request_list')
        
    req = get_object_or_404(StockRequest, id=rid)
    if request.method == 'POST':
        sent_qty = decimal.Decimal(request.POST.get('sent_qty', req.requested_qty))
        req.sent_qty = sent_qty
        req.status = 'APPROVED'
        req.approved_by = request.user
        req.shipped_at = timezone.now()
        req.save()
        
        # Notify Store Owner
        create_notification(
            user=req.requested_by,
            title="Stock Shipped",
            message=f"Request for {req.product.name} (Approved: {sent_qty}kg) has been shipped.",
            level='SUCCESS',
            link='/stock-requests/'
        )
        
        messages.success(request, f"Request approved. Shipment recorded for {sent_qty}kg.")
    return redirect('stock_request_list')


@login_required
@require_profile
def stock_request_receive(request, rid):
    """Store Owner records the quantity received and verifies against sent quantity."""
    profile = get_profile(request.user)
    req = get_object_or_404(StockRequest, id=rid, store=profile.store)
    
    if req.status != 'APPROVED':
        messages.error(request, "This request is not in 'Approved & Shipped' status.")
        return redirect('stock_request_list')
        
    if request.method == 'POST':
        received_qty = decimal.Decimal(request.POST.get('received_qty', 0))
        req.received_qty = received_qty
        req.received_at = timezone.now()
        
        # Update actual stock inventory
        product = req.product
        product.stock_quantity += received_qty
        product.save(update_fields=['stock_quantity'])
        
        StockLog.objects.create(
            store=profile.store,
            product=product,
            movement='IN',
            quantity=received_qty,
            balance=product.stock_quantity,
            reference=f"Stock Req #{req.id} Received",
            created_by=request.user
        )
        
        # Reconciliation Logic
        if received_qty != req.sent_qty:
            req.status = 'DISCREPANCY'
            req.save()
            # ALERT ADMIN & AM IMMEDIATELY
            notify_area_managers(
                store=profile.store,
                title="⚠️ STOCK DISCREPANCY ALERT",
                message=f"Discrepancy detected at {profile.store.name} for {product.name}. Sent: {req.sent_qty}kg, Received: {received_qty}kg.",
                level='DANGER',
                link='/stock-requests/',
                include_admin=True
            )
            messages.warning(request, "Stock discrepancy detected! Admin and Area Manager have been notified.")
        else:
            req.status = 'RECEIVED'
            req.save()
            messages.success(request, f"Inventory updated successfully with {received_qty}kg.")
            
    return redirect('stock_request_list')


@login_required
@require_profile
def mark_notifications_read(request):
    """Utility API to mark all notifications as read for current user."""
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return JsonResponse({'status': 'ok'})
